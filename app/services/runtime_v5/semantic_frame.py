from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from app.services.runtime_v5.conversation_hints import ConversationHints
from app.services.runtime_v5.conversation_state import ConversationState


@dataclass(frozen=True)
class SemanticFrame:
    """LLM semantic-understanding contract.

    This frame describes the utterance only. It intentionally excludes
    capability, provider, runtime, permission, identity, and credential fields.
    """

    speech_act: str = "ask"
    topic: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    operation: str = "ask"
    requested_output: str = "natural_text"
    parameters: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    ambiguities: tuple[str, ...] = ()
    source: str = "semantic_understanding_contract_v1"

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["ambiguities"] = list(self.ambiguities)
        return data


def understand_semantics(
    *,
    message: str,
    state: ConversationState,
    hints: ConversationHints,
) -> SemanticFrame:
    """Build a SemanticFrame.

    Production can replace this deterministic V1 body with an LLM call, but the
    output contract must stay limited to semantic understanding.
    """

    speech_act = _speech_act(hints=hints, state=state)
    topic = _topic(hints=hints, state=state)
    operation = _operation(hints=hints, speech_act=speech_act)
    parameters = _parameters(message=message, state=state, hints=hints, operation=operation)
    target = _target(hints=hints, parameters=parameters, state=state)
    ambiguities = _ambiguities(hints=hints, state=state, operation=operation, parameters=parameters)
    return SemanticFrame(
        speech_act=speech_act,
        topic=topic,
        target=target,
        operation=operation,
        requested_output=hints.requested_output_hint or "natural_text",
        parameters=parameters,
        confidence=0.72 if ambiguities else _confidence(hints=hints, state=state),
        ambiguities=ambiguities,
    )


def _speech_act(*, hints: ConversationHints, state: ConversationState) -> str:
    if hints.is_cancel_word:
        return "cancel"
    if hints.is_confirmation_word and (state.pending_confirmation.kind or state.pending_clarification.kind):
        return "confirm"
    if hints.is_confirmation_word:
        return "answer"
    if hints.is_action_request:
        return "request_action"
    if hints.is_followup_reference:
        return "followup"
    return "ask"


def _topic(*, hints: ConversationHints, state: ConversationState) -> str:
    if hints.domain_hint == "People":
        return "people"
    if hints.domain_hint == "Knowledge":
        return "knowledge"
    if hints.domain_hint == "Communication":
        return "communication"
    if state.active_topic:
        return state.active_topic
    return "conversation"


def _operation(*, hints: ConversationHints, speech_act: str) -> str:
    if speech_act in {"cancel", "confirm"}:
        return speech_act
    if speech_act == "request_action":
        return "action_request"
    if speech_act == "followup" and hints.operation_hint == "ask":
        return "followup"
    if hints.operation_hint:
        return hints.operation_hint
    return "ask"


def _parameters(
    *,
    message: str,
    state: ConversationState,
    hints: ConversationHints,
    operation: str,
) -> dict[str, Any]:
    params: dict[str, Any] = {"raw_message": message}
    if hints.target_hint:
        params["target_hint"] = hints.target_hint
    if hints.scope_hint:
        params["scope_hint"] = hints.scope_hint
    if hints.scope_hint == "department" and hints.target_hint:
        params["organization_unit"] = hints.target_hint
    person_name = _person_candidate(message)
    if person_name:
        params["person_name"] = person_name
    field = _requested_field(hints.target_hint)
    if field:
        params["field"] = field
    gender = _gender_filter(hints.target_hint)
    if gender:
        params["filters"] = {"gender": gender}
    if operation in {"followup", "list"} and state.previous_result_reference.collection_type:
        params["previous_result"] = {
            "result_type": state.previous_result_reference.result_type,
            "collection_type": state.previous_result_reference.collection_type,
            "count": state.previous_result_reference.count,
            "filters": state.previous_result_reference.filters,
            "field_projection": state.previous_result_reference.field_projection,
        }
        if state.previous_result_reference.filters and "filters" not in params:
            params["filters"] = _normalized_previous_filters(state.previous_result_reference.filters)
    current_object = state.active_object if state.previous_result_reference.object_type == "person" else {}
    if current_object and field and not person_name:
        current_name = str(current_object.get("name") or "").strip()
        if current_name:
            params["person_name"] = current_name
            person_name = current_name
    if state.previous_result_reference.field_projection and person_name:
        params["inherited_field_projection"] = state.previous_result_reference.field_projection
    if current_object:
        params["current_object"] = state.active_object
    return params


def _target(
    *,
    hints: ConversationHints,
    parameters: dict[str, Any],
    state: ConversationState,
) -> dict[str, Any]:
    target: dict[str, Any] = {}
    if hints.scope_hint:
        target["scope"] = hints.scope_hint
    if hints.target_hint:
        target["value"] = hints.target_hint
    if "field" in parameters:
        target["field"] = parameters["field"]
    if state.previous_result_reference.collection_type and hints.is_followup_reference:
        target["previous_result"] = state.previous_result_reference.collection_type
    return target


def _normalized_previous_filters(filters: dict[str, Any]) -> dict[str, Any]:
    if filters.get("filter") == "gender" and filters.get("value"):
        return {"gender": filters.get("value")}
    if filters.get("filter") == "field_present" and filters.get("value"):
        return {"field_present": filters.get("value")}
    if filters.get("filter") == "name_prefix" and filters.get("value"):
        return {"name_prefix": filters.get("value")}
    return dict(filters)


def _ambiguities(
    *,
    hints: ConversationHints,
    state: ConversationState,
    operation: str,
    parameters: dict[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    if hints.is_confirmation_word and not state.pending_confirmation.kind and not state.pending_clarification.kind:
        issues.append("confirmation_without_pending_state")
    if operation == "field_lookup" and parameters.get("field") and not parameters.get("person_name") and not state.previous_result_reference.object_type:
        issues.append("missing_person_target")
    if hints.is_action_request and not state.previous_result_reference.has_items and not hints.target_hint:
        issues.append("missing_action_target")
    return tuple(issues)


def _confidence(*, hints: ConversationHints, state: ConversationState) -> float:
    if hints.is_followup_reference and state.active_domain:
        return 0.88
    if hints.domain_hint in {"People", "Knowledge"}:
        return 0.86
    if hints.is_action_request:
        return 0.78
    return 0.66


def _requested_field(target_hint: str) -> str:
    aliases = {
        "电话": "mobile",
        "手机号": "mobile",
        "号码": "mobile",
        "邮箱": "email",
        "职位": "job_title",
        "岗位": "job_title",
        "性别": "gender",
    }
    return aliases.get(target_hint, "")


def _gender_filter(target_hint: str) -> str:
    if target_hint == "male":
        return "male"
    if target_hint == "female":
        return "female"
    return ""


def _person_candidate(message: str) -> str:
    compact = re.sub(r"\s+", "", str(message or ""))
    has_field = any(token in compact for token in ("电话", "手机号", "号码", "邮箱", "职位", "岗位", "性别", "是男是女"))
    if any(token in compact for token in ("公司", "部门", "我们", "男生", "男性", "女生", "女性", "有谁")):
        return ""
    if compact.startswith(("他", "她")):
        return ""
    if not has_field and any(token in compact for token in ("多少", "几位", "几个")):
        return ""
    match = re.match(r"(?P<name>[\u4e00-\u9fff]{2,4})(?:的)?(?:电话|手机号|号码|邮箱|职位|岗位|性别|是男是女).*", compact)
    if match:
        return match.group("name").removesuffix("的")
    if compact.startswith(("那", "那么")):
        name = re.sub(r"^(那|那么)", "", compact)
        name = re.sub(r"呢$", "", name)
        return name if 2 <= len(name) <= 4 else ""
    match = re.match(r"(?P<name>[\u4e00-\u9fff]{2,4})呢$", compact)
    return match.group("name") if match else ""
