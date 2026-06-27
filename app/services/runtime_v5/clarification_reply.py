from __future__ import annotations

from dataclasses import dataclass, field

from app.services.runtime_v5.models import ResultContext


@dataclass(frozen=True)
class ClarificationReply:
    is_reply: bool
    resolved_message: str = ""
    filled_params: dict[str, str] = field(default_factory=dict)


def resolve_clarification_reply(message: str, result_context: ResultContext | None) -> ClarificationReply:
    if result_context is None:
        return ClarificationReply(is_reply=False)
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    if metadata.get("execution_status") != "clarification":
        return ClarificationReply(is_reply=False)
    text = str(message or "").strip()
    if not text:
        return ClarificationReply(is_reply=False)

    missing_params = tuple(str(param) for param in metadata.get("missing_params", []) if str(param))
    options = metadata.get("clarification_options") if isinstance(metadata.get("clarification_options"), list) else []
    filled = _filled_params(text, missing_params=missing_params, options=options)
    if not filled:
        return ClarificationReply(is_reply=False)

    operation = str(metadata.get("operation") or result_context.result_type.replace("_clarification", "")).strip()
    resolved_message = _resolved_message(operation=operation, filled=filled, original=text)
    return ClarificationReply(is_reply=True, resolved_message=resolved_message, filled_params=filled)


def _filled_params(text: str, *, missing_params: tuple[str, ...], options: list) -> dict[str, str]:
    compact = text.replace(" ", "")
    filled: dict[str, str] = {}
    allowed_values = {
        str(option.get("value"))
        for option in options
        if isinstance(option, dict) and str(option.get("value") or "")
    }
    for param in missing_params:
        value = _value_for_param(param, compact)
        if value and (not allowed_values or value in allowed_values or value in {"current_department"}):
            filled[param] = value
    return filled


def _value_for_param(param: str, compact: str) -> str:
    if param == "scope":
        if any(token in compact for token in ("我的", "自己", "个人", "我")):
            return "self"
        if any(token in compact for token in ("当前部门", "本部门", "部门", "团队", "小组")):
            return "department"
        if any(token in compact for token in ("全公司", "公司", "全部", "所有")):
            return "company"
    if param in {"department", "target_department_id"}:
        if any(token in compact for token in ("当前部门", "本部门", "部门", "团队", "小组")):
            return "current_department"
    if param in {"time", "time_range", "start", "end"}:
        if "今天" in compact:
            return "today"
        if any(token in compact for token in ("本周", "这周", "本星期", "这星期")):
            return "this_week"
        if any(token in compact for token in ("本月", "这个月")):
            return "this_month"
    if param in {"person", "target", "target_user", "recipient", "transfer_user_id", "add_sign_user_ids", "cc_user_ids"}:
        if any(token in compact for token in ("指定人", "指定人员", "某人", "这个人")):
            return "user"
    if param in {"object", "project", "approval_item", "task_guid", "document_id"}:
        if any(token in compact for token in ("这个", "第一个", "对象", "项目", "审批", "任务", "文档")):
            return "object"
    return ""


def _resolved_message(*, operation: str, filled: dict[str, str], original: str) -> str:
    base = _operation_phrase(operation)
    scope = _scope_phrase(filled)
    time_range = _time_phrase(filled)
    parts = [part for part in (scope + base, time_range, original) if part]
    return " ".join(parts).strip()


def _operation_phrase(operation: str) -> str:
    return {
        "task_query": "任务",
        "calendar_query": "日程",
        "approval_query": "审批",
        "people_lookup": "人员",
        "department_members": "成员",
    }.get(operation, operation or "查询")


def _scope_phrase(filled: dict[str, str]) -> str:
    values = set(filled.values())
    if "company" in values:
        return "查看全公司"
    if "department" in values or "current_department" in values:
        return "查看部门"
    if "self" in values:
        return "查看我的"
    return "查看"


def _time_phrase(filled: dict[str, str]) -> str:
    values = set(filled.values())
    if "today" in values:
        return "今天"
    if "this_week" in values:
        return "本周"
    if "this_month" in values:
        return "本月"
    return ""
