from dataclasses import dataclass
import json
import shutil
import subprocess

from app.services.tools.base import ToolContext, ToolRequest


LARK_CLI_TIMEOUT_SECONDS = 5
REQUIRED_LARK_CLI_COMMANDS = (
    "api",
    "approval",
    "attendance",
    "base",
    "calendar",
    "contact",
    "docs",
    "drive",
    "event",
    "im",
    "mail",
    "minutes",
    "okr",
    "task",
    "vc",
    "wiki",
)


@dataclass(frozen=True)
class CliCommandResult:
    output: str
    returncode: int | None


def execute_devops_tool(context: ToolContext, request: ToolRequest) -> str:
    if request.tool_name == "feishu_cli_status":
        return _feishu_cli_status()
    if request.tool_name == "feishu_cli_doctor":
        return _feishu_cli_doctor()
    raise NotImplementedError(f"DevOps provider is not wired for tool: {request.tool_name}")


def _feishu_cli_status() -> str:
    path = shutil.which("lark-cli")
    if not path:
        return "飞书 CLI 未安装或不在 PATH 中：lark-cli"

    version_result = _run_lark_cli(path, "--version")
    help_result = _run_lark_cli(path, "--help")
    version = _first_line(version_result.output)
    help_output = help_result.output
    commands = _available_command_names(help_output)
    command_text = "、".join(commands[:12]) if commands else "未解析到命令列表"
    required_status = _required_command_status(commands)
    return (
        "飞书 CLI 可用。\n"
        f"路径: {path}\n"
        f"版本: {version or '未知'}\n"
        f"版本命令退出码: {_returncode_text(version_result.returncode)}\n"
        f"帮助命令退出码: {_returncode_text(help_result.returncode)}\n"
        f"关键命令: {required_status}\n"
        f"可用命令: {command_text}"
    )


def _feishu_cli_doctor() -> str:
    path = shutil.which("lark-cli")
    if not path:
        return "飞书 CLI 未安装或不在 PATH 中：lark-cli"

    result = run_feishu_cli_doctor_offline(path)
    return (
        "飞书 CLI 离线健康检查完成。\n"
        f"路径: {path}\n"
        f"退出码: {_returncode_text(result.returncode)}\n"
        f"摘要: {_safe_cli_output_summary(result.output)}"
    )


def run_feishu_cli_doctor_offline(path: str, *, profile: str | None = None) -> CliCommandResult:
    args = ["doctor", "--offline"]
    if profile:
        args = ["--profile", profile, *args]
    return _run_lark_cli(path, *args)


def _run_lark_cli(path: str, *args: str) -> CliCommandResult:
    try:
        completed = subprocess.run(
            [path, *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=LARK_CLI_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CliCommandResult(output=f"执行失败: {exc}", returncode=None)
    output = (completed.stdout or completed.stderr or "").strip()
    return CliCommandResult(output=output, returncode=getattr(completed, "returncode", 0))


def _first_line(output: str) -> str:
    return output.splitlines()[0].strip() if output else ""


def _available_command_names(help_output: str) -> list[str]:
    commands: list[str] = []
    in_section = False
    for line in help_output.splitlines():
        if line.strip() == "Available Commands:":
            in_section = True
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped:
            if commands:
                break
            continue
        commands.append(stripped.split()[0])
    return commands


def _required_command_status(commands: list[str]) -> str:
    available = set(commands)
    return "、".join(f"{name}={'ok' if name in available else 'missing'}" for name in REQUIRED_LARK_CLI_COMMANDS)


def _returncode_text(returncode: int | None) -> str:
    return "timeout/error" if returncode is None else str(returncode)


def _safe_cli_output_summary(output: str) -> str:
    structured = _structured_doctor_summary(output)
    if structured:
        return structured
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "无输出"
    return " / ".join(lines[:8])


def _structured_doctor_summary(output: str) -> str | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return None
    parts = [f"ok={str(payload.get('ok')).lower()}"]
    for item in checks[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        status = str(item.get("status") or "").strip()
        message = str(item.get("message") or "").strip()
        if not name:
            continue
        detail = f"{name}={status or 'unknown'}"
        if message:
            detail += f" ({message})"
        parts.append(detail)
    workspace = str(payload.get("workspace") or "").strip()
    if workspace:
        parts.append(f"workspace={workspace}")
    return "; ".join(parts) if len(parts) > 1 else None
