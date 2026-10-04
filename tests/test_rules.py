import json
from pathlib import Path

import pytest

from micro_llm.cascade.rules import parse
from micro_llm.evals.compare import compare
from micro_llm.intent import Intent, PolicyFlag

ROOT = Path(__file__).parent.parent
DEV = ROOT / "devset" / "intent_dev.jsonl"
EVALS = ROOT / "evals" / "intent_cases.jsonl"


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_devset_is_valid_and_separate_from_evals() -> None:
    dev = _lines(DEV)
    assert len(dev) == 100
    assert len({c["id"] for c in dev}) == 100
    for case in dev:
        Intent.model_validate(case["expected"])
    eval_requests = {c["request"] for c in _lines(EVALS)}
    assert not eval_requests & {c["request"] for c in dev}


def test_rules_regression_on_devset() -> None:
    """Garde-fou : une règle ajoutée ne doit pas casser les cas déjà réussis."""
    exact = 0
    loosened = 0
    for case in _lines(DEV):
        score = compare(Intent.model_validate(case["expected"]), parse(str(case["request"])).intent)
        exact += score.exact
        loosened += len(score.loosened)
    assert exact >= 99
    assert loosened == 0


def test_publique_is_public() -> None:
    # « publique » ne contient pas « public » (p-u-b-l-i-q-u-e).
    service = parse("Mon appli ghcr.io/acme/x publique.").intent.services[0]
    assert service.exposure == "public"


def test_internet_is_not_internal() -> None:
    assert parse("Caddy exposé sur Internet.").intent.services[0].exposure == "public"


def test_outbound_internet_is_not_exposure() -> None:
    service = parse("Lance ghcr.io/acme/x, il doit télécharger des pages sur Internet.")
    assert service.intent.services[0].exposure == "internal"
    assert service.intent.services[0].needs_internet is True


def test_docker_socket_path_is_not_host_filesystem() -> None:
    flags = parse("ghcr.io/acme/x doit monter /var/run/docker.sock.").intent.policy_flags
    assert flags == {PolicyFlag.DOCKER_SOCKET}


def test_image_name_does_not_trigger_catalog_words() -> None:
    kinds = {s.kind for s in parse("Lance ghcr.io/acme/blog en interne.").intent.services}
    assert kinds == {"custom"}


def test_described_service_is_reported_as_doubt() -> None:
    result = parse("un cloud perso pour mes photos, accessible depuis Internet")
    assert result.intent.services[0].kind == "nextcloud"
    assert result.doubts


def test_nothing_recognized_raises() -> None:
    with pytest.raises(ValueError, match="aucun service"):
        parse("Bonjour, ça va ?")
