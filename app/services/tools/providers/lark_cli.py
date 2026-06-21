from dataclasses import dataclass
import inspect
import json
import subprocess
from typing import Any


@dataclass(frozen=True)
class LarkCliExecutionContract:
    engine_name: str
    role: str
    allowed_caller: str
    allowed_caller_module: str
    accepts_business_capability: bool
    schedules_tools: bool
    performs_action_execution: bool


LARK_CLI_EXECUTION_CONTRACT = LarkCliExecutionContract(
    engine_name="lark_cli",
    role="action_execution",
    allowed_caller="feishu_mcp_provider",
    allowed_caller_module="app.services.tools.providers.feishu_mcp",
    accepts_business_capability=False,
    schedules_tools=False,
    performs_action_execution=True,
)


def run_lark_cli_json(args: list[str], *, action: str) -> Any:
    _ensure_allowed_caller()
    result = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Feishu CLI {action} failed: {error or result.returncode}")
    return json_from_cli_stdout(result.stdout)


def run_lark_cli_json_loose(args: list[str], *, action: str, timeout: int = 30) -> dict[str, Any]:
    _ensure_allowed_caller()
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{action} failed: {(result.stderr or result.stdout or '').strip()}")
    payload = json_from_cli_stdout(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{action} returned unsupported output.")
    if payload.get("ok") is False:
        raise RuntimeError(f"{action} failed: {payload.get('error') or payload}")
    if payload.get("code") not in (None, 0):
        raise RuntimeError(f"{action} failed: {payload.get('msg') or payload}")
    return payload


def run_lark_cli_text(args: list[str], *, action: str, timeout: int = 30) -> str:
    _ensure_allowed_caller()
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{action} failed: {(result.stderr or result.stdout or '').strip()}")
    return (result.stdout or result.stderr or "").strip()


def json_from_cli_stdout(stdout: str) -> Any:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        return {}


def _ensure_allowed_caller() -> None:
    frame = inspect.currentframe()
    caller = frame.f_back.f_back if frame and frame.f_back else None
    module_name = str(caller.f_globals.get("__name__", "")) if caller else ""
    if module_name == LARK_CLI_EXECUTION_CONTRACT.allowed_caller_module:
        return
    raise PermissionError(
        "Feishu CLI action execution must be scheduled by the Feishu MCP provider; "
        f"direct caller is not allowed: {module_name or 'unknown'}"
    )
