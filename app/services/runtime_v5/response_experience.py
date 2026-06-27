from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.runtime_v5.models import CommandPlan, ComposedAnswer


@dataclass(frozen=True)
class ResponseExperience:
    """Display-neutral conversation affordances for RuntimeResult consumers."""

    contextual_intro: str = ""
    followup_suggestions: tuple[str, ...] = ()
    display_mode: str = "natural"


def build_response_experience(
    *,
    command_plan: CommandPlan,
    result_type: str,
    result_metadata: dict[str, Any],
    composed: ComposedAnswer,
) -> ResponseExperience:
    return ResponseExperience(
        contextual_intro=_contextual_intro(
            command_plan=command_plan,
            result_type=result_type,
            result_metadata=result_metadata,
            composed=composed,
        ),
        followup_suggestions=_followup_suggestions(
            result_type=result_type,
            result_metadata=result_metadata,
            command_plan=command_plan,
            composed=composed,
        ),
    )


def _contextual_intro(
    *,
    command_plan: CommandPlan,
    result_type: str,
    result_metadata: dict[str, Any],
    composed: ComposedAnswer,
) -> str:
    for source in (composed.metadata, result_metadata):
        value = source.get("contextual_intro") if isinstance(source, dict) else ""
        if str(value or "").strip():
            return str(value).strip()[:240]
    enrichment = _command_enrichment_metadata(command_plan)
    objective = str(enrichment.get("objective") or "").strip()
    if objective and not _is_placeholder_objective(objective):
        return f"我先按你的问题整理当前可见结果：{objective}。"
    scope = _scope_label(str(command_plan.intent_result.data_scope or ""))
    if result_metadata.get("contextual_intro") is False:
        return ""
    if command_plan.intent in {"task_query"} or result_type in {"task_list", "task_query"}:
        return f"我先按{scope}能看到的任务整理一下，明细和操作都放在下面。"
    if command_plan.intent in {"calendar_query"} or result_type in {"calendar_event_list", "calendar_query"}:
        return f"我先按{scope}能看到的日程整理一下，后面可以继续追问冲突、空档或详情。"
    if command_plan.intent in {"approval_query"} or result_type in {"approval_list", "approval_query"}:
        return "我先把待处理审批按 AI 判断分组，你可以直接看高风险、需关注或可通过项。"
    if result_type in {"workspace_aggregation_summary", "workspace_summary"}:
        return f"实时明细还没完全接入时，我先基于{scope}已授权的认知数据给你一个可用概览。"
    return ""


def _is_placeholder_objective(value: str) -> bool:
    compact = str(value or "").strip("。；; ").lower()
    return compact in {"query", "search", "lookup", "present", "answer", "查询", "搜索", "了解", "获取"}


def _followup_suggestions(
    *,
    result_type: str,
    result_metadata: dict[str, Any],
    command_plan: CommandPlan,
    composed: ComposedAnswer,
) -> tuple[str, ...]:
    for source in (composed.metadata, result_metadata):
        raw = source.get("followup_suggestions") if isinstance(source, dict) else None
        suggestions = _normalize_suggestions(raw)
        if suggestions:
            return suggestions

    if result_type in {"task_list", "task_query"}:
        return ("第一个详情", "按优先级排一下", "还有哪些快到期")
    if result_type in {"approval_list", "approval_query"}:
        return ("展开高风险项", "第一个详情", "为什么这么判断")
    if result_type == "approval_detail":
        return ("为什么这么判断", "缺什么证据", "下一步怎么处理")
    if result_type in {"calendar_event_list", "calendar_query"}:
        return ("今天空档在哪", "还有冲突吗", "发给相关人")
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return ("全部列出", "哪些是男性", "哪些是女性")
    if result_type in {"workspace_aggregation_summary", "workspace_summary"}:
        return ("展开", "第一个是什么", "哪些需要关注")
    if result_type in {"runtime_action", "task_complete", "approval_approve", "approval_reject"}:
        return ()

    if command_plan.intent_result.question_type in {"analysis", "insight", "decision"}:
        return ("展开依据", "有哪些风险", "下一步建议")
    return ("展开", "第一个是什么", "下一步呢")


def _normalize_suggestions(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = [part.strip() for part in value.replace(" / ", "/").split("/")]
    elif isinstance(value, (list, tuple)):
        candidates = [str(item).strip() for item in value]
    else:
        return ()
    seen: set[str] = set()
    normalized: list[str] = []
    for item in candidates:
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item[:32])
        if len(normalized) >= 4:
            break
    return tuple(normalized)


def _command_enrichment_metadata(command_plan: CommandPlan) -> dict[str, Any]:
    entities = command_plan.intent_result.entities if isinstance(command_plan.intent_result.entities, dict) else {}
    enrichment = entities.get("command_enrichment")
    return enrichment if isinstance(enrichment, dict) else {}


def _scope_label(data_scope: str) -> str:
    normalized = data_scope.strip().lower()
    return {
        "self": "你当前",
        "person": "指定人员范围内",
        "user": "指定人员范围内",
        "department": "部门范围内",
        "company": "公司范围内",
        "organization": "公司范围内",
        "project": "项目范围内",
    }.get(normalized, "当前范围内")
