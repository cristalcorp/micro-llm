"""Mesure de l'étage « règles » de la cascade (D-016), sans modèle, avec les indicateurs de D-015.

uv run python -m micro_llm.evals.run_rules --cases devset/intent_dev.jsonl
"""

import argparse
import json
import resource
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from micro_llm.cascade.rules import parse
from micro_llm.evals.compare import FIELDS, CaseScore, compare
from micro_llm.evals.run import DEFAULT_CASES, DEFAULT_OUT, CaseResult, summarize
from micro_llm.intent import Intent


def run_case(case: dict[str, object]) -> tuple[CaseResult, tuple[str, ...]]:
    expected = Intent.model_validate(case["expected"])
    start = time.perf_counter()
    score: CaseScore | None = None
    error: str | None = None
    doubts: tuple[str, ...] = ()
    try:
        result = parse(str(case["request"]))
        score = compare(expected, result.intent)
        doubts = result.doubts
    except ValueError as exc:
        error = str(exc)
    latency = time.perf_counter() - start
    return (
        CaseResult(
            id=str(case["id"]),
            category=str(case["category"]),
            raw="",
            json_ok=score is not None,
            schema_ok=score is not None,
            error=error,
            score=score,
            expected_fields=len(FIELDS) * len(expected.services),
            latency_s=round(latency, 6),
            prompt_tokens=0,
            output_tokens=0,
            output_tokens_per_s=0.0,
        ),
        doubts,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mesure l'étage règles de la cascade.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines()]
    rows = [run_case(case) for case in cases]
    results = [r for r, _ in rows]
    for result, doubts in rows:
        mark = "=" if result.score and result.score.exact else ("~" if result.score else "x")
        detail = result.error or ", ".join(result.score.wrong if result.score else ())
        print(f"{mark} {result.id} {detail}{'  [doute]' if doubts else ''}")

    summary = summarize(results)
    flagged = [r for r, d in rows if d]
    meta = {
        "date": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "engine": "rules",
        "cases_file": args.cases.name,
        # Pic de mémoire du processus Python entier (Linux : en Kio).
        "rss_peak_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        "doubts": {
            "cases": len(flagged),
            "of_which_wrong": sum(1 for r in flagged if not (r.score and r.score.exact)),
            "wrong_without_doubt": sum(
                1 for r, d in rows if not d and not (r.score and r.score.exact)
            ),
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{meta['date']}-rules-{args.cases.stem}.json"
    report = {"meta": meta, "summary": summary, "cases": [asdict(r) for r, _ in rows]}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta | {"summary": summary["total"]}, indent=2, ensure_ascii=False))
    print(f"Rapport : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
