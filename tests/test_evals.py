import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from micro_llm.evals.compare import compare
from micro_llm.evals.prompt import SYSTEM_PROMPT, extract_json
from micro_llm.evals.run import CaseResult, summarize
from micro_llm.intent import Intent

CASES = Path(__file__).parent.parent / "evals" / "intent_cases.jsonl"


def _intent(*services: Mapping[str, object]) -> Intent:
    return Intent.model_validate({"services": list(services)})


WORDPRESS: dict[str, object] = {
    "name": "wordpress",
    "kind": "wordpress",
    "exposure": "public",
    "persistent": True,
}
MARIADB: dict[str, object] = {"name": "mariadb", "kind": "mariadb", "persistent": True}


def test_names_and_order_do_not_matter() -> None:
    expected = _intent(WORDPRESS | {"depends_on": ["mariadb"]}, MARIADB)
    got = _intent(MARIADB | {"name": "db"}, WORDPRESS | {"name": "blog", "depends_on": ["db"]})
    score = compare(expected, got)
    assert score.exact
    assert score.fields_ok == score.fields_total == 10


def test_wrong_field_is_reported_and_loosening_is_flagged() -> None:
    expected = _intent(MARIADB)
    got = _intent(MARIADB | {"exposure": "public", "needs_internet": True})
    score = compare(expected, got)
    assert not score.exact
    assert set(score.wrong) == {"mariadb.exposure", "mariadb.needs_internet"}
    assert len(score.loosened) == 2


def test_stricter_answer_is_wrong_but_not_loosened() -> None:
    score = compare(_intent(WORDPRESS), _intent(WORDPRESS | {"exposure": "internal"}))
    assert score.wrong == ("wordpress.exposure",)
    assert score.loosened == ()


def test_missing_and_extra_services() -> None:
    expected = _intent(WORDPRESS | {"depends_on": ["mariadb"]}, MARIADB)
    got = _intent(WORDPRESS, {"name": "db", "kind": "postgres"})
    score = compare(expected, got)
    assert score.missing == ("mariadb",)
    assert score.extra == ("postgres",)
    assert score.wrong == ("wordpress.depends_on",)
    assert score.fields_ok == 4
    assert score.fields_total == 10


def test_custom_services_are_matched_by_image() -> None:
    api: dict[str, object] = {"name": "api", "kind": "custom", "image": "ghcr.io/acme/api"}
    other: dict[str, object] = {"name": "api", "kind": "custom", "image": "ghcr.io/acme/other"}
    score = compare(_intent(api), _intent(other))
    assert score.missing == ("custom:ghcr.io/acme/api",)
    assert score.extra == ("custom:ghcr.io/acme/other",)


def test_missed_policy_flag_is_a_loosening() -> None:
    flagged: dict[str, object] = MARIADB | {"policy_flags": ["privileged"]}
    assert compare(_intent(flagged), _intent(MARIADB)).loosened == (
        "mariadb: policy_flag privileged manquant",
    )
    assert compare(_intent(flagged), _intent(WORDPRESS)).loosened == (
        "mariadb: policy_flag privileged manquant (service absent)",
    )


@pytest.mark.parametrize(
    "text",
    [
        '{"services": []}',
        'Voici :\n```json\n{"services": []}\n```',
        '<think>{"brouillon": 1}</think>\n{"services": []}',
    ],
)
def test_extract_json_tolerates_wrapping(text: str) -> None:
    assert extract_json(text) == {"services": []}


@pytest.mark.parametrize("text", ["", "pas de JSON", '{"services": [', "} {"])
def test_extract_json_returns_none_on_garbage(text: str) -> None:
    assert extract_json(text) is None


def test_prompt_example_is_valid_and_not_from_eval_set() -> None:
    example = SYSTEM_PROMPT.split("Réponse : ", 1)[1].strip()
    Intent.model_validate_json(example)
    requests = [json.loads(line)["request"] for line in CASES.read_text().splitlines()]
    assert not any(r in SYSTEM_PROMPT for r in requests)


def _result(case_id: str, category: str, score_from: Intent | None) -> CaseResult:
    expected = _intent(MARIADB)
    return CaseResult(
        id=case_id,
        category=category,
        raw="",
        json_ok=score_from is not None,
        schema_ok=score_from is not None,
        error=None if score_from else "pas de JSON lisible",
        score=compare(expected, score_from) if score_from else None,
        expected_fields=5,
        latency_s=1.0,
        prompt_tokens=10,
        output_tokens=10,
        output_tokens_per_s=10.0,
    )


def test_summary_counts_invalid_answers_as_wrong_fields() -> None:
    results = [
        _result("a", "simple", _intent(MARIADB)),
        _result("b", "simple", None),
        _result("c", "multi", _intent(MARIADB | {"exposure": "public"})),
    ]
    summary = summarize(results)
    total = summary["total"]
    assert (total["cases"], total["schema_ok"], total["exact"]) == (3, 2, 1)
    assert (total["fields_ok"], total["fields_total"]) == (9, 15)
    assert total["loosened"] == 1
    assert summary["simple"]["exact"] == 1
    assert summary["multi"]["exact"] == 0
