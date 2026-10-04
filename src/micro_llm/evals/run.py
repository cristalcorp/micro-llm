"""Mesure d'un modèle GGUF sur les cas d'`evals/` : justesse, latence, mémoire.

Lance lui-même `llama-server` sur 127.0.0.1 (jamais une autre adresse : D-001), envoie
chaque demande, valide la réponse avec le schéma de l'intention puis la compare à
l'attendu par structure. La mémoire est lue dans /proc (Linux).
Les sous-processus sont lancés sans shell, avec un binaire choisi par l'utilisateur.

    uv run python -m micro_llm.evals.run --server <llama-server> --model <fichier.gguf>
"""

import argparse
import hashlib
import http.client
import json
import socket
import statistics
import subprocess  # nosec B404
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from micro_llm.evals.compare import FIELDS, CaseScore, compare
from micro_llm.evals.prompt import build_messages, extract_json
from micro_llm.intent import Intent

HOST = "127.0.0.1"
DEFAULT_CASES = Path(__file__).resolve().parents[3] / "evals" / "intent_cases.jsonl"
DEFAULT_OUT = Path.home() / ".cache" / "micro-llm" / "results"


@dataclass(frozen=True)
class CaseResult:
    id: str
    category: str
    raw: str
    json_ok: bool
    schema_ok: bool
    error: str | None
    score: CaseScore | None
    expected_fields: int
    latency_s: float
    prompt_tokens: int
    output_tokens: int
    output_tokens_per_s: float


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        port: int = s.getsockname()[1]
        return port


def _proc_kib(pid: int, field: str) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1])
    raise RuntimeError(f"{field} absent de /proc/{pid}/status")


@dataclass
class Server:
    process: subprocess.Popen[bytes]
    port: int

    def request(self, method: str, path: str, body: Any = None, timeout: float = 600) -> Any:
        conn = http.client.HTTPConnection(HOST, self.port, timeout=timeout)
        try:
            payload = None if body is None else json.dumps(body).encode()
            conn.request(method, path, payload, {"Content-Type": "application/json"})
            response = conn.getresponse()
            data = response.read()
            if response.status != 200:
                raise RuntimeError(f"{path} : HTTP {response.status} {data[:300]!r}")
            return json.loads(data)
        finally:
            conn.close()


@contextmanager
def llama_server(binary: Path, model: Path, threads: int, ctx: int, log: Path) -> Iterator[Server]:
    port = _free_port()
    cmd = [str(binary), "-m", str(model), "--host", HOST, "--port", str(port), "--jinja"]
    cmd += ["-t", str(threads), "-c", str(ctx), "-ngl", "0", "--parallel", "1"]
    with log.open("wb") as out:
        process = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)  # nosec B603
    server = Server(process, port)
    try:
        deadline = time.monotonic() + 180
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"llama-server s'est arrêté, voir {log}")
            try:
                server.request("GET", "/health", timeout=2)
                break
            except OSError, RuntimeError:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"llama-server ne répond pas, voir {log}") from None
                time.sleep(0.5)
        yield server
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def run_case(server: Server, case: dict[str, Any], schema: dict[str, Any] | None) -> CaseResult:
    body: dict[str, Any] = {
        "messages": build_messages(case["request"]),
        "temperature": 0,
        "max_tokens": 768,
        # Qwen3 : pas de mode réflexion pour cette tâche ; ignoré par les autres modèles.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "intent", "schema": schema},
        }
    start = time.perf_counter()
    response = server.request("POST", "/v1/chat/completions", body)
    latency = time.perf_counter() - start
    raw: str = response["choices"][0]["message"]["content"] or ""
    timings = response.get("timings", {})

    data = extract_json(raw)
    error: str | None = None
    score: CaseScore | None = None
    schema_ok = False
    expected = Intent.model_validate(case["expected"])
    if data is None:
        error = "pas de JSON lisible"
    else:
        try:
            got = Intent.model_validate(data)
            schema_ok = True
            score = compare(expected, got)
        except ValidationError as exc:
            error = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())[
                :500
            ]
    return CaseResult(
        id=case["id"],
        category=case["category"],
        raw=raw,
        json_ok=data is not None,
        schema_ok=schema_ok,
        error=error,
        score=score,
        expected_fields=len(FIELDS) * len(expected.services),
        latency_s=round(latency, 3),
        prompt_tokens=int(timings.get("prompt_n", 0)),
        output_tokens=int(timings.get("predicted_n", 0)),
        output_tokens_per_s=round(float(timings.get("predicted_per_second", 0.0)), 2),
    )


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    def block(rs: list[CaseResult]) -> dict[str, Any]:
        scores = [r.score for r in rs if r.score is not None]
        latencies = sorted(r.latency_s for r in rs)
        return {
            "cases": len(rs),
            "json_ok": sum(r.json_ok for r in rs),
            "schema_ok": sum(r.schema_ok for r in rs),
            "exact": sum(s.exact for s in scores),
            "fields_ok": sum(s.fields_ok for s in scores),
            # Un cas sans intention valide compte tous ses champs comme faux.
            "fields_total": sum(r.expected_fields for r in rs),
            "loosened": sum(len(s.loosened) for s in scores),
            "latency_p50_s": round(statistics.median(latencies), 2) if latencies else None,
            "latency_max_s": latencies[-1] if latencies else None,
            "output_tokens_per_s": round(statistics.mean(r.output_tokens_per_s for r in rs), 1)
            if rs
            else None,
        }

    categories = sorted({r.category for r in results})
    return {"total": block(results)} | {
        c: block([r for r in results if r.category == c]) for c in categories
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _version(binary: Path) -> str:
    out = subprocess.run(  # nosec B603
        [str(binary), "--version"], capture_output=True, text=True, timeout=30, check=False
    )
    return (out.stdout + out.stderr).strip().splitlines()[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mesure un modèle GGUF sur les cas d'evals/.")
    parser.add_argument("--server", type=Path, required=True, help="binaire llama-server")
    parser.add_argument("--model", type=Path, required=True, help="fichier GGUF")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--mode", choices=["free", "schema"], default="free")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--ctx", type=int, default=4096)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines()]
    schema = Intent.model_json_schema() if args.mode == "schema" else None
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{stamp}-{args.model.stem}-{args.mode}-t{args.threads}"

    results: list[CaseResult] = []
    with llama_server(
        args.server, args.model, args.threads, args.ctx, args.out / f"{stem}.log"
    ) as server:
        rss_loaded = _proc_kib(server.process.pid, "VmRSS")
        for case in cases:
            result = run_case(server, case, schema)
            results.append(result)
            mark = "=" if result.score and result.score.exact else ("~" if result.score else "x")
            print(f"{mark} {result.id} {result.latency_s:6.2f}s {result.error or ''}", flush=True)
        rss_peak = _proc_kib(server.process.pid, "VmHWM")

    summary = summarize(results)
    meta: dict[str, Any] = {
        "date": stamp,
        "model": args.model.name,
        "model_sha256": _sha256(args.model),
        "llama_server": _version(args.server),
        "mode": args.mode,
        "threads": args.threads,
        "ctx": args.ctx,
        "rss_loaded_mib": rss_loaded // 1024,
        "rss_peak_mib": rss_peak // 1024,
    }
    report = {"meta": meta, "summary": summary, "cases": [asdict(r) for r in results]}
    path = args.out / f"{stem}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta | {"summary": summary["total"]}, indent=2))
    print(f"Rapport : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
