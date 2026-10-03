import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from micro_llm.intent import Intent, PolicyFlag, ServiceKind

CASES = Path(__file__).parent.parent / "evals" / "intent_cases.jsonl"


def _service(**overrides: object) -> dict[str, object]:
    return {"name": "db", "kind": "postgres"} | overrides


def _cases() -> list[dict[str, object]]:
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()]


def test_eval_set_has_thirty_unique_cases() -> None:
    ids = [case["id"] for case in _cases()]
    assert len(ids) == 30
    assert len(set(ids)) == 30


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["id"]))
def test_every_expected_intent_is_valid(case: dict[str, object]) -> None:
    Intent.model_validate(case["expected"])


def test_policy_cases_carry_flags() -> None:
    for case in _cases():
        intent = Intent.model_validate(case["expected"])
        assert bool(intent.policy_flags) == (case["category"] == "politique"), case["id"]


def test_minimal_intent_gets_safe_defaults() -> None:
    intent = Intent.model_validate({"services": [_service()]})
    service = intent.services[0]
    assert service.exposure == "internal"
    assert service.persistent is False
    assert service.needs_internet is False
    assert intent.policy_flags == frozenset()


def test_dangerous_options_have_no_field() -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate({"services": [_service(privileged=True)]})
    with pytest.raises(ValidationError):
        Intent.model_validate({"services": [_service(volumes=["/var/run/docker.sock:/s"])]})


def test_unknown_policy_flag_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate({"services": [_service()], "policy_flags": ["cap_sys_admin"]})
    assert PolicyFlag("docker_socket") is PolicyFlag.DOCKER_SOCKET


def test_empty_intent_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate({"services": []})


@pytest.mark.parametrize("name", ["DB", "1db", "db_main", "", "a" * 64, "db;rm"])
def test_invalid_service_names_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate({"services": [_service(name=name)]})


def test_custom_requires_image_and_catalog_forbids_it() -> None:
    with pytest.raises(ValidationError, match="doit préciser son image"):
        Intent.model_validate({"services": [_service(kind="custom")]})
    with pytest.raises(ValidationError, match="fixée par le code"):
        Intent.model_validate({"services": [_service(image="postgres")]})
    intent = Intent.model_validate(
        {"services": [_service(kind="custom", image="ghcr.io/acme/api")]}
    )
    assert intent.services[0].kind is ServiceKind.CUSTOM


@pytest.mark.parametrize(
    "image", ["ghcr.io/acme/api:latest", "acme/api@sha256:abc", "Acme/Api", "acme//api"]
)
def test_image_cannot_carry_tag_digest_or_odd_syntax(image: str) -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate({"services": [_service(kind="custom", image=image)]})


def test_duplicate_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="en double"):
        Intent.model_validate({"services": [_service(), _service()]})


def test_unknown_dependency_is_rejected() -> None:
    with pytest.raises(ValidationError, match="inconnus"):
        Intent.model_validate({"services": [_service(depends_on=["cache"])]})


def test_self_dependency_is_rejected() -> None:
    with pytest.raises(ValidationError, match="lui-même"):
        Intent.model_validate({"services": [_service(depends_on=["db"])]})


def test_dependency_cycle_is_rejected() -> None:
    services = [
        _service(name="a", depends_on=["b"]),
        _service(name="b", depends_on=["c"]),
        _service(name="c", depends_on=["a"]),
    ]
    with pytest.raises(ValidationError, match="circulaire"):
        Intent.model_validate({"services": services})


def test_too_many_services_are_rejected() -> None:
    services = [_service(name=f"s{i}") for i in range(21)]
    with pytest.raises(ValidationError):
        Intent.model_validate({"services": services})
