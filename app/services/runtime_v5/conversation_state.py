from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.runtime_v5.models import ResultContext, RuntimeContext


@dataclass(frozen=True)
class PreviousResultReference:
    result_type: str = ""
    domain: str = ""
    topic: str = ""
    target_label: str = ""
    object_type: str = ""
    collection_type: str = ""
    count: int = 0
    filters: dict[str, Any] = field(default_factory=dict)
    field_projection: str = ""
    presentation: str = ""
    has_items: bool = False
    object_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingConversationState:
    kind: str = ""
    intent: str = ""
    strategy: str = ""
    message: str = ""
    missing_params: tuple[str, ...] = ()
    confirmation_token: str = ""


@dataclass(frozen=True)
class AssistantQuestionState:
    kind: str = ""
    prompt: str = ""
    target: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversationState:
    active_domain: str = ""
    active_topic: str = ""
    active_object: dict[str, Any] = field(default_factory=dict)
    active_collection: dict[str, Any] = field(default_factory=dict)
    last_user_goal: str = ""
    last_assistant_question: AssistantQuestionState = field(default_factory=AssistantQuestionState)
    pending_confirmation: PendingConversationState = field(default_factory=PendingConversationState)
    pending_clarification: PendingConversationState = field(default_factory=PendingConversationState)
    previous_result_reference: PreviousResultReference = field(default_factory=PreviousResultReference)
    presentation_preference: str = "natural_text"
    user_profile: dict[str, Any] = field(default_factory=dict)
    source_contract: dict[str, bool] = field(default_factory=dict)


def build_conversation_state(context: RuntimeContext) -> ConversationState:
    """Normalize legacy session/result objects into the Conversation First state.

    V1 keeps legacy objects alive, but only this builder should interpret them
    for Command Engine routing.
    """

    result_ref = _previous_result_reference(context.result_context)
    pending_confirmation = _pending_confirmation(context.session_context)
    pending_clarification = _pending_clarification(context.result_context)
    assistant_question = _assistant_question(context.result_context, pending_clarification=pending_clarification)
    domain = result_ref.domain or _pending_domain(pending_confirmation) or _pending_domain(pending_clarification)
    topic = result_ref.topic or pending_confirmation.intent or pending_clarification.intent
    return ConversationState(
        active_domain=domain,
        active_topic=topic,
        active_object=_active_object(result_ref),
        active_collection=_active_collection(result_ref),
        last_user_goal=_last_user_goal(context.result_context),
        last_assistant_question=assistant_question,
        pending_confirmation=pending_confirmation,
        pending_clarification=pending_clarification,
        previous_result_reference=result_ref,
        presentation_preference=_presentation_preference(context),
        user_profile=_profile_payload(context),
        source_contract={
            "result_context_consumed_by_builder": context.result_context is not None,
            "pending_action_consumed_by_builder": bool(pending_confirmation.kind),
            "pending_clarification_consumed_by_builder": bool(pending_clarification.kind),
        },
    )


def _previous_result_reference(result_context: ResultContext | None) -> PreviousResultReference:
    if result_context is None:
        return PreviousResultReference()
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    domain = _normalize_domain(str(metadata.get("entity_domain") or _domain_for_result_type(result_context.result_type)))
    people_filter = metadata.get("people_filter") if isinstance(metadata.get("people_filter"), dict) else {}
    collection_type = _collection_type(result_context=result_context, metadata=metadata, domain=domain)
    return PreviousResultReference(
        result_type=result_context.result_type,
        domain=domain,
        topic=_topic_for_result_type(result_context.result_type, domain=domain),
        target_label=_target_label(result_context=result_context, metadata=metadata),
        object_type=_object_type(result_context=result_context, domain=domain),
        collection_type=collection_type,
        count=int(result_context.count or len(result_context.items or ())),
        filters=dict(people_filter),
        field_projection=_field_projection(metadata),
        presentation=str(metadata.get("result_context_presentation") or ""),
        has_items=bool(result_context.items),
        object_payload=_object_payload(result_context=result_context, metadata=metadata, domain=domain),
    )


def _pending_confirmation(session_context: dict[str, Any]) -> PendingConversationState:
    pending = session_context.get("runtime_v5_pending_action")
    if not isinstance(pending, dict):
        state = session_context.get("runtime_v5_state")
        actions = state.get("actions") if isinstance(state, dict) and isinstance(state.get("actions"), list) else []
        pending = next(
            (
                action.get("metadata", {}).get("pending_action")
                for action in reversed(actions)
                if isinstance(action, dict)
                and action.get("status") == "waiting_confirmation"
                and isinstance(action.get("metadata", {}).get("pending_action"), dict)
            ),
            {},
        )
    if not isinstance(pending, dict) or not pending:
        return PendingConversationState()
    return PendingConversationState(
        kind="confirmation",
        intent=str(pending.get("intent") or ""),
        strategy=str(pending.get("strategy") or ""),
        message=str(pending.get("message") or ""),
        missing_params=tuple(str(item) for item in pending.get("missing_params", []) if str(item)),
        confirmation_token=str(pending.get("confirmation_token") or pending.get("id") or ""),
    )


def _pending_clarification(result_context: ResultContext | None) -> PendingConversationState:
    if result_context is None:
        return PendingConversationState()
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    if metadata.get("execution_status") != "clarification":
        return PendingConversationState()
    return PendingConversationState(
        kind="clarification",
        intent=str(metadata.get("operation") or result_context.result_type.replace("_clarification", "")),
        strategy=str(metadata.get("operation") or result_context.result_type.replace("_clarification", "")),
        message=str(metadata.get("clarification_prompt") or result_context.answer or ""),
        missing_params=tuple(str(item) for item in metadata.get("missing_params", []) if str(item)),
    )


def _assistant_question(
    result_context: ResultContext | None,
    *,
    pending_clarification: PendingConversationState,
) -> AssistantQuestionState:
    if pending_clarification.kind:
        return AssistantQuestionState(
            kind="clarification",
            prompt=pending_clarification.message,
            target=pending_clarification.intent,
        )
    if result_context is None:
        return AssistantQuestionState()
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    prompt = str(metadata.get("last_assistant_question") or metadata.get("clarification_prompt") or "").strip()
    if not prompt:
        return AssistantQuestionState()
    return AssistantQuestionState(kind="question", prompt=prompt, target=str(metadata.get("question_target") or ""))


def _active_object(result_ref: PreviousResultReference) -> dict[str, Any]:
    if not result_ref.object_type:
        return {}
    return {
        **result_ref.object_payload,
        "type": result_ref.object_type,
        "domain": result_ref.domain,
        "topic": result_ref.topic,
    }


def _active_collection(result_ref: PreviousResultReference) -> dict[str, Any]:
    if not result_ref.collection_type:
        return {}
    return {
        "type": result_ref.collection_type,
        "domain": result_ref.domain,
        "topic": result_ref.topic,
        "target_label": result_ref.target_label,
        "count": result_ref.count,
        "filters": result_ref.filters,
    }


def _last_user_goal(result_context: ResultContext | None) -> str:
    if result_context is None:
        return ""
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    if metadata.get("field_projection") == "count_only" or metadata.get("result_context_presentation") == "summary":
        return "count"
    if metadata.get("field_projection") in {"name_only", "detail"}:
        return "list"
    return str(metadata.get("people_query_mode") or metadata.get("operation") or "")


def _presentation_preference(context: RuntimeContext) -> str:
    message = str(context.current_message or "")
    compact = message.replace(" ", "")
    if any(token in compact for token in ("只回答", "只要", "只说")):
        return "short_answer"
    if any(token in compact for token in ("名单", "明细", "展开", "列出")):
        return "sidepanel"
    return "natural_text"


def _profile_payload(context: RuntimeContext) -> dict[str, Any]:
    profile = context.profile
    if profile is None:
        return {}
    return {
        "preferred_address": getattr(profile, "preferred_address", ""),
        "style": getattr(profile, "style", ""),
        "verbosity": getattr(profile, "verbosity", ""),
    }


def _domain_for_result_type(result_type: str) -> str:
    if result_type in {"people_search", "department_members", "organization_snapshot"}:
        return "People"
    if result_type in {"company_profile_knowledge", "knowledge_search", "docs_read"}:
        return "Knowledge"
    if result_type in {"runtime_pending_confirmation", "runtime_waiting_input", "runtime_action"}:
        return "Action"
    return ""


def _normalize_domain(domain: str) -> str:
    aliases = {
        "people": "People",
        "knowledge": "Knowledge",
        "action": "Action",
        "communication": "Communication",
    }
    text = str(domain or "").strip()
    return aliases.get(text.lower(), text)


def _topic_for_result_type(result_type: str, *, domain: str) -> str:
    if domain == "People":
        return "people"
    if domain == "Knowledge":
        return "knowledge"
    return result_type


def _object_type(*, result_context: ResultContext, domain: str) -> str:
    if domain == "People" and result_context.count == 1:
        return "person"
    if domain == "Knowledge" and result_context.count == 1:
        return "knowledge_item"
    return ""


def _field_projection(metadata: dict[str, Any]) -> str:
    if metadata.get("field_projection"):
        return str(metadata.get("field_projection") or "")
    if metadata.get("people_query_field"):
        return str(metadata.get("people_query_field") or "")
    frame = metadata.get("people_context_frame") if isinstance(metadata.get("people_context_frame"), dict) else {}
    return str(frame.get("current_requested_field") or "")


def _object_payload(*, result_context: ResultContext, metadata: dict[str, Any], domain: str) -> dict[str, Any]:
    if domain != "People" or result_context.count != 1 or not result_context.items:
        return {}
    item = result_context.items[0] if isinstance(result_context.items[0], dict) else {}
    frame = metadata.get("people_context_frame") if isinstance(metadata.get("people_context_frame"), dict) else {}
    name = str(frame.get("current_person") or item.get("name") or "").strip()
    if not name:
        return {}
    return {
        "name": name,
        "requested_field": str(frame.get("current_requested_field") or metadata.get("people_query_field") or ""),
        "visible_fields": tuple(str(field) for field in frame.get("visible_fields", ()) if str(field)),
        "identity_resolution": str(frame.get("identity_resolution") or metadata.get("identity_resolution") or ""),
    }


def _collection_type(*, result_context: ResultContext, metadata: dict[str, Any], domain: str) -> str:
    if domain == "People" and result_context.result_type == "department_members":
        return "department_people"
    if domain == "People" and result_context.count != 1:
        people_filter = metadata.get("people_filter") if isinstance(metadata.get("people_filter"), dict) else {}
        if people_filter.get("filter") == "gender":
            return "filtered_people"
        return "company_people"
    if domain == "Knowledge" and result_context.count:
        return "knowledge_results"
    return ""


def _target_label(*, result_context: ResultContext, metadata: dict[str, Any]) -> str:
    resolution = metadata.get("organization_resolution") if isinstance(metadata.get("organization_resolution"), dict) else {}
    resolved_name = str(resolution.get("resolved_name") or "").strip()
    if result_context.result_type == "department_members" and resolved_name:
        return resolved_name
    for key in ("keyword", "query"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    for key in ("resolved_name", "query", "normalized_query"):
        value = str(resolution.get(key) or "").strip()
        if value:
            return value
    if result_context.result_type == "department_members" and result_context.answer:
        text = str(result_context.answer or "")
        if text.startswith("「") and "」" in text:
            return text.split("」", 1)[0].removeprefix("「").strip()
    return ""


def _pending_domain(pending: PendingConversationState) -> str:
    if not pending.kind:
        return ""
    if pending.strategy.startswith("people") or pending.strategy == "organization_snapshot":
        return "People"
    if pending.strategy in {"general_query", "docs_read", "wiki_search", "drive_list"}:
        return "Knowledge"
    if pending.strategy in {"message_send", "mail_draft_create"}:
        return "Communication"
    return "Action"
