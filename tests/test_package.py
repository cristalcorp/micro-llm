import micro_llm


def test_version_is_exposed() -> None:
    assert micro_llm.__version__ == "9.9.9"
