import json
from pathlib import Path

from micro_llm.cascade.datagen import generate
from micro_llm.intent import Intent

ROOT = Path(__file__).parent.parent


def _requests(path: Path) -> set[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return {json.loads(line)["request"] for line in lines}


def test_generation_is_deterministic() -> None:
    assert generate(50, seed=3) == generate(50, seed=3)
    assert generate(50, seed=3) != generate(50, seed=4)


def test_every_generated_intent_is_valid_and_unique() -> None:
    rows = generate(500, seed=0)
    for row in rows:
        Intent.model_validate(row["expected"])
    assert len({row["request"] for row in rows}) == 500


def test_no_overlap_with_evals_or_devset() -> None:
    reserved = _requests(ROOT / "evals" / "intent_cases.jsonl")
    reserved |= _requests(ROOT / "devset" / "intent_dev.jsonl")
    assert not reserved & {str(row["request"]) for row in generate(2000, seed=0)}


def test_hard_phenomena_are_produced() -> None:
    seen = {tag for row in generate(2000, seed=0) for tag in row["phenomena"]}  # type: ignore[attr-defined]
    assert {"described", "coref", "negation", "relation", "far_flag", "english"} <= seen
