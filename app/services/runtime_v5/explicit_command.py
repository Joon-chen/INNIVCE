from __future__ import annotations

from dataclasses import dataclass

from app.services.runtime_v5.models import IntentResult, RuntimeContext


@dataclass(frozen=True)
class ExplicitCommandSpec:
    command: str
    intent: str
    question_type: str = "query"
    scope: str = "self"
    family: str = "system"
    description: str = ""
    aliases: tuple[str, ...] = ()


_COMMAND_SPECS: tuple[ExplicitCommandSpec, ...] = (
    ExplicitCommandSpec(
        command="/help",
        intent="smalltalk",
        family="help",
        description="查看可用显式命令和使用方式。",
        aliases=("/帮助",),
    ),
    ExplicitCommandSpec(
        command="/reset",
        intent="smalltalk",
        family="session_control",
        description="清空当前会话上下文。",
        aliases=("/重置", "/清空会话"),
    ),
    ExplicitCommandSpec(
        command="/system status",
        intent="runtime_status",
        family="observability",
        description="查看系统运行状态摘要。",
        aliases=("/system diagnostics", "/系统 状态", "/系统 诊断"),
    ),
    ExplicitCommandSpec(
        command="/runtime trace",
        intent="action_trace",
        family="observability",
        description="查看最近 Runtime 决策和动作轨迹。",
        aliases=("/运行 追踪", "/trace"),
    ),
    ExplicitCommandSpec(
        command="/capability registry",
        intent="governance_view",
        family="governance",
        description="查看能力目录、能力清册和治理状态。",
        aliases=("/capability catalog", "/能力 目录", "/能力 清册"),
    ),
    ExplicitCommandSpec(
        command="/policy identity",
        intent="runtime_status",
        family="policy",
        description="查看当前身份、授权和权限边界。",
        aliases=("/权限 身份", "/auth status", "/授权 状态"),
    ),
)
_COMMAND_ALIASES: dict[str, ExplicitCommandSpec] = {
    alias: spec
    for spec in _COMMAND_SPECS
    for alias in (spec.command, *spec.aliases)
}


def explicit_command_intent(question: str, context: RuntimeContext) -> IntentResult | None:
    text = _normalize(question)
    if not text.startswith("/"):
        return None
    spec = _COMMAND_ALIASES.get(text)
    if spec is None:
        return _unknown_explicit_command(question=question, normalized=text, context=context)
    return IntentResult(
        question_type=spec.question_type,  # type: ignore[arg-type]
        intent=spec.intent,
        data_scope=spec.scope,  # type: ignore[arg-type]
        entities={
            "fallback_answer": _help_text() if spec.family == "help" else "",
            "explicit_command": {
                "command": spec.command,
                "family": spec.family,
                "description": spec.description,
            },
            "command_frame": {
                "utterance_type": "explicit_command",
                "intent": spec.intent,
                "question_type": spec.question_type,
                "scope": spec.scope,
                "confidence": 1.0,
                "route_path": "explicit_command",
                "route_reason": f"explicit_command:{text}",
                "command_family": spec.family,
                "company_id": str(context.runtime_scope.active_company_id or ""),
            }
        },
        missing_params=(),
        confidence=1.0,
        canonical_question=question,
    )


def _unknown_explicit_command(*, question: str, normalized: str, context: RuntimeContext) -> IntentResult:
    return IntentResult(
        question_type="query",
        intent="smalltalk",
        data_scope="self",
        entities={
            "fallback_answer": f"我没有识别这个显式命令：{normalized}。\n\n{_help_text()}",
            "explicit_command": {
                "command": normalized,
                "family": "unknown",
                "description": "未知显式命令，不进入业务工具。",
            },
            "command_frame": {
                "utterance_type": "explicit_command",
                "intent": "smalltalk",
                "question_type": "query",
                "scope": "self",
                "confidence": 1.0,
                "route_path": "explicit_command_unknown",
                "route_reason": f"unknown_explicit_command:{normalized}",
                "command_family": "unknown",
                "company_id": str(context.runtime_scope.active_company_id or ""),
            },
        },
        missing_params=(),
        confidence=1.0,
        canonical_question=question,
    )


def _help_text() -> str:
    lines = ["可用显式命令："]
    for spec in _COMMAND_SPECS:
        aliases = f"（别名：{'、'.join(spec.aliases)}）" if spec.aliases else ""
        lines.append(f"- {spec.command}：{spec.description}{aliases}")
    lines.append("普通自然语言不用加 /，会交给 Command LLM 结合上下文理解。")
    return "\n".join(lines)


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())
