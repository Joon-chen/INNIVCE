from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from app.services.runtime_v5.models import IntentResult, ResultContext, RuntimeContext


@dataclass(frozen=True)
class DomainQuery:
    domain: str
    operation_kind: str = "read"
    subject: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    fields: tuple[str, ...] = ()
    scope: str = "self"
    context_ref: dict[str, Any] = field(default_factory=dict)
    output_mode: str = "answer"
    presentation_hint: str = "text"
    risk_hint: str = "low"
    evidence_requirement: str = "source"

    def payload(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "operation_kind": self.operation_kind,
            "subject": self.subject,
            "filters": self.filters,
            "fields": list(self.fields),
            "scope": self.scope,
            "context_ref": self.context_ref,
            "output_mode": self.output_mode,
            "presentation_hint": self.presentation_hint,
            "risk_hint": self.risk_hint,
            "evidence_requirement": self.evidence_requirement,
        }


def intent_with_domain_query(intent: IntentResult, context: RuntimeContext) -> IntentResult:
    from dataclasses import replace

    query = build_domain_query(intent=intent, context=context)
    if query is None:
        return intent
    entities = dict(intent.entities)
    entities["domain_query"] = query.payload()
    return replace(intent, entities=entities)


def build_domain_query(*, intent: IntentResult, context: RuntimeContext) -> DomainQuery | None:
    if intent.intent in {"people_lookup", "department_members", "organization_snapshot"}:
        return build_people_domain_query(intent=intent, context=context)
    return None


def build_people_domain_query(*, intent: IntentResult, context: RuntimeContext) -> DomainQuery:
    entities = intent.entities if isinstance(intent.entities, dict) else {}
    text = context.current_message or intent.canonical_question
    fields = _people_fields(text, entities=entities)
    output_mode = _people_output_mode(intent=intent, text=text)
    subject = _people_subject(intent=intent, text=text, entities=entities)
    filters = _people_filters(text=text, entities=entities)
    context_ref = _people_context_ref(context.result_context)
    presentation = "sidepanel" if output_mode == "detail" else "text"
    if output_mode == "list" and subject.get("type") != "person":
        presentation = "text"
    return DomainQuery(
        domain="people",
        operation_kind="read",
        subject=subject,
        filters=filters,
        fields=fields,
        scope=str(intent.data_scope or "organization"),
        context_ref=context_ref,
        output_mode=output_mode,
        presentation_hint=presentation,
        risk_hint="low",
        evidence_requirement="source",
    )


def domain_query_payload(entities: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(entities, dict):
        return {}
    value = entities.get("domain_query")
    return value if isinstance(value, dict) else {}


def domain_query_fields(entities: dict[str, Any] | None) -> tuple[str, ...]:
    query = domain_query_payload(entities)
    fields = query.get("fields")
    if not isinstance(fields, list):
        return ()
    return tuple(str(field) for field in fields if str(field).strip())


def domain_query_output_mode(entities: dict[str, Any] | None) -> str:
    return str(domain_query_payload(entities).get("output_mode") or "")


def _people_subject(*, intent: IntentResult, text: str, entities: dict[str, Any]) -> dict[str, Any]:
    if intent.intent == "organization_snapshot":
        return {"type": "organization"}
    if intent.intent == "department_members":
        return {"type": "group", "department": str(entities.get("keyword") or "").strip()}
    keyword = str(entities.get("keyword") or "").strip()
    if keyword:
        return {"type": "person", "name": keyword}
    if _has_context_pronoun(text):
        return {"type": "previous_result"}
    return {"type": "person"}


def _people_filters(*, text: str, entities: dict[str, Any]) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    compact = _compact(text)
    gender = _gender_filter(text)
    if gender:
        filters["gender"] = gender
    if any(token in compact for token in ("有谁的号码", "谁的号码", "有谁的电话", "谁的电话")):
        filters["field_present"] = "mobile"
    surname = _surname_filter(text)
    if surname:
        filters["name_prefix"] = surname
    mode = str(entities.get("people_query_mode") or "")
    if mode:
        filters["query_mode"] = mode
    return filters


def _people_fields(text: str, *, entities: dict[str, Any]) -> tuple[str, ...]:
    fields: list[str] = []
    compact = _compact(text)
    if any(token in compact for token in ("岗位", "职位", "职务")):
        fields.append("title")
    if any(token in compact for token in ("直属上级", "上级", "领导")):
        fields.append("leader")
    if any(token in compact for token in ("电话", "号码", "手机号", "手机")):
        fields.append("mobile")
    if "邮箱" in compact:
        fields.append("email")
    if any(token in compact for token in ("男还是女", "女还是男", "男性还是女性", "性别")):
        fields.append("gender")
    explicit = str(entities.get("people_query_field") or "").strip()
    if explicit and explicit not in fields:
        fields.append(explicit)
    return tuple(fields)


def _people_output_mode(*, intent: IntentResult, text: str) -> str:
    compact = _compact(text)
    mode = str((intent.entities if isinstance(intent.entities, dict) else {}).get("people_query_mode") or "")
    if mode in {"count", "count_only", "gender_count", "title_count"}:
        return "count"
    if mode in {"list", "gender_list", "title_list"}:
        return "list"
    if any(token in compact for token in ("只要数量", "只需要数量", "只告诉我数量", "只回答数量")):
        return "count"
    if any(token in compact for token in ("全部列出", "全部显示", "名单", "分别是谁", "都有谁")):
        return "list"
    if any(token in compact for token in ("详情", "明细", "打开侧边栏", "展开详情")):
        return "detail"
    if intent.intent == "organization_snapshot":
        return "count"
    return "answer"


def _people_context_ref(result_context: ResultContext | None) -> dict[str, Any]:
    if result_context is None:
        return {}
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    frame = metadata.get("people_context_frame") if isinstance(metadata.get("people_context_frame"), dict) else {}
    item = result_context.items[0] if len(result_context.items) == 1 and isinstance(result_context.items[0], dict) else {}
    return {
        "result_type": result_context.result_type,
        "count": result_context.count or len(result_context.items),
        "current_person": str(frame.get("current_person") or item.get("name") or ""),
        "current_requested_field": str(frame.get("current_requested_field") or metadata.get("people_query_field") or ""),
    }


def _gender_filter(text: str) -> str:
    compact = _compact(text)
    if any(token in compact for token in ("男生", "男性", "男的", "男员工")):
        return "male"
    if any(token in compact for token in ("女生", "女性", "女的", "女员工")):
        return "female"
    return ""


def _surname_filter(text: str) -> str:
    compact = _compact(text)
    match = re.search(r"姓([\u4e00-\u9fff])", compact)
    return match.group(1) if match else ""


def _has_context_pronoun(text: str) -> bool:
    compact = _compact(text)
    return any(token in compact for token in ("他", "她", "那个人", "这个人", "刚才那个人"))


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())
