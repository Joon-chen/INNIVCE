import ast
from pathlib import Path


def test_tools_services_do_not_call_feishu_http_or_llm_clients() -> None:
    offenders: list[str] = []
    forbidden_imports = [
        "app.services.feishu",
        "app.services.feishu.client",
        "app.services.llm",
        "httpx",
        "requests",
        "lark_oapi",
        "openai",
    ]
    forbidden_text = [
        "FeishuClient",
        "Raw HTTP",
        "OpenAI",
        "DeepSeek",
        "Qwen",
        "complete_text",
        "complete_deepseek_text",
    ]

    excluded_tools_paths = {Path("app/services/tools/providers/feishu_mcp.py")}
    for path in Path("app/services/tools").rglob("*.py"):
        if "__pycache__" in path.parts or path in excluded_tools_paths:
            continue
        text = path.read_text()
        tree = ast.parse(text)
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        for marker in forbidden_imports:
            if any(module.startswith(marker) for module in imported_modules):
                offenders.append(f"{path}:{marker}")
        for marker in forbidden_text:
            if marker in text:
                offenders.append(f"{path}:{marker}")

    assert offenders == []
