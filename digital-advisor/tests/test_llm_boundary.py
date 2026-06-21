from pathlib import Path


def test_openai_compatible_client_is_created_only_in_llm_boundary() -> None:
    offenders: list[str] = []
    for path in Path("app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text()
        if "from openai import OpenAI" not in text and "import openai" not in text:
            continue
        if not str(path).startswith("app/services/llm/"):
            offenders.append(str(path))

    assert offenders == []
