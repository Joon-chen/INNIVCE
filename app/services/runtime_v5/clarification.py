from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services.runtime_v5.models import IntentResult, RuntimeContext


@dataclass(frozen=True)
class ClarificationOption:
    param: str
    label: str
    value: str

    def payload(self) -> dict[str, str]:
        return {"param": self.param, "label": self.label, "value": self.value}


@dataclass(frozen=True)
class ClarificationGuide:
    prompt: str
    reason: str
    missing_params: tuple[str, ...]
    options: tuple[ClarificationOption, ...]
    next_step: str

    def option_payloads(self) -> list[dict[str, str]]:
        return [option.payload() for option in self.options]


def build_clarification_guide(
    *,
    context: RuntimeContext,
    intent: IntentResult,
    fallback: str,
) -> ClarificationGuide:
    missing_params = tuple(str(param) for param in (intent.missing_params or ()) if str(param))
    reason = "missing_params" if missing_params else "low_confidence"
    prompt = _clarification_prompt(intent=intent, fallback=fallback, missing_params=missing_params)
    options = _dedupe_options(
        option
        for param in missing_params
        for option in _options_for_param(param, context=context)
    )
    next_step = _next_step(missing_params)
    return ClarificationGuide(
        prompt=prompt,
        reason=reason,
        missing_params=missing_params,
        options=tuple(options),
        next_step=next_step,
    )


def _clarification_prompt(*, intent: IntentResult, fallback: str, missing_params: tuple[str, ...]) -> str:
    entities = intent.entities if isinstance(intent.entities, dict) else {}
    prompt = entities.get("clarification_prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    if missing_params:
        return _next_step(missing_params)
    return str(fallback or "").strip() or _low_confidence_prompt(intent)


def _next_step(missing_params: tuple[str, ...]) -> str:
    if missing_params:
        return "请补充：" + "、".join(_missing_param_label(param) for param in missing_params)
    return "把要看的对象、范围或时间补一句就行。"


def _low_confidence_prompt(intent: IntentResult) -> str:
    if intent.intent == "external_information_query":
        return "这类问题需要外部实时信息能力；当前还没有接入实时联网查询，所以我不能可靠回答。"
    if intent.question_type == "action":
        return "我先不执行动作，避免改错真实数据。你把动作、对象和内容再连起来说一句，我再继续。"
    if intent.intent in {"task_query", "calendar_query", "approval_query", "mail_query", "message_query"}:
        return "我还没对准要查的范围。你可以直接说“我的、部门、公司、某个人”，再加上要看的内容。"
    if intent.intent in {"people_lookup", "department_members", "organization_snapshot"}:
        return "我还没对准要找的人或组织范围。你可以直接说姓名、部门，或要看的组织层级。"
    if intent.intent in {"general_analysis", "risk_analysis", "decision_advice", "general_query"}:
        return "我可以继续分析，但现在依据还不够聚焦。你可以直接说想看的主题、范围，或者让我先按公司视角概览。"
    return "我在。你可以继续自然说，我会结合上下文判断；如果要查数据，把对象和范围带上会更准。"


def _options_for_param(param: str, *, context: RuntimeContext) -> list[ClarificationOption]:
    if param == "scope":
        return [
            ClarificationOption(param="scope", label="我的", value="self"),
            ClarificationOption(param="scope", label="部门", value="department"),
            ClarificationOption(param="scope", label="公司", value="company"),
        ]
    if param in {"department", "target_department_id"}:
        options = [ClarificationOption(param=param, label="指定部门", value="department")]
        if context.runtime_scope.active_department_id:
            options.insert(0, ClarificationOption(param=param, label="当前部门", value="current_department"))
        return options
    if param in {"time", "time_range", "start", "end"}:
        return [
            ClarificationOption(param=param, label="今天", value="today"),
            ClarificationOption(param=param, label="本周", value="this_week"),
            ClarificationOption(param=param, label="本月", value="this_month"),
        ]
    if param in {"person", "target", "target_user", "recipient", "transfer_user_id", "add_sign_user_ids", "cc_user_ids"}:
        return [ClarificationOption(param=param, label="指定人员", value="user")]
    if param in {"object", "project", "approval_item", "task_guid", "document_id"}:
        return [ClarificationOption(param=param, label="指定对象", value="object")]
    return []


def _dedupe_options(options: Iterable[ClarificationOption]) -> list[ClarificationOption]:
    seen: set[tuple[str, str]] = set()
    result: list[ClarificationOption] = []
    for option in options:
        key = (option.param, option.value)
        if key in seen:
            continue
        seen.add(key)
        result.append(option)
    return result


def _missing_param_label(param: str) -> str:
    return {
        "person": "人员",
        "target": "对象",
        "target_user": "人员",
        "target_user_id": "人员",
        "department": "部门",
        "target_department_id": "部门",
        "time": "时间",
        "time_range": "时间范围",
        "start": "开始时间",
        "end": "结束时间",
        "summary": "标题/内容",
        "approval_item": "审批单",
        "task_guid": "任务",
        "document_id": "文档",
        "message": "消息内容",
        "recipient": "接收人",
        "target_type": "发送对象",
        "text": "消息内容",
        "to": "收件人",
        "subject": "主题",
        "body": "正文",
        "chat_id": "会话",
        "scope": "范围",
        "query": "查询内容",
        "task_query_criteria": "要看的任务范围或条件",
        "calendar_query_criteria": "要看的日程范围或时间",
        "approval_query_criteria": "要看的审批范围或条件",
    }.get(str(param), "必要信息")
