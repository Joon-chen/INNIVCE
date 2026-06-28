from __future__ import annotations

from dataclasses import asdict, dataclass, field
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
import os
import re
from typing import Any

from app.core.config import settings
from app.services.llm.gateway import LLMGateway
from app.services.llm.routing_policy import llm_route_for_task
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

    frame = _deterministic_semantic_frame(message=message, state=state, hints=hints)
    return _llm_semantic_frame(message=message, state=state, hints=hints, fallback=frame) or frame


def _deterministic_semantic_frame(
    *,
    message: str,
    state: ConversationState,
    hints: ConversationHints,
) -> SemanticFrame:
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


def _llm_semantic_frame(
    *,
    message: str,
    state: ConversationState,
    hints: ConversationHints,
    fallback: SemanticFrame,
) -> SemanticFrame | None:
    if _running_tests() or not settings.bot_llm_semantics_enabled:
        return None
    if not _should_try_llm_semantics(fallback=fallback, state=state, hints=hints):
        return None
    prompt = _semantic_prompt(message=message, state=state, hints=hints, fallback=fallback)
    timeout_seconds = max(0.5, llm_route_for_task("command_intent").latency_budget_ms / 1000.0)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="semantic-frame-llm")
    future = executor.submit(lambda: LLMGateway().complete_task_text(prompt, task_type="command_intent", temperature=0.0))
    try:
        raw = future.result(timeout=timeout_seconds) or ""
    except TimeoutError:
        future.cancel()
        return None
    except Exception:
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    payload = _parse_json_object(raw)
    if not payload:
        return None
    frame = _semantic_frame_from_payload(payload=payload, message=message, state=state, fallback=fallback)
    if frame is None:
        return None
    return frame


def _should_try_llm_semantics(*, fallback: SemanticFrame, state: ConversationState, hints: ConversationHints) -> bool:
    if fallback.speech_act in {"confirm", "cancel"}:
        return False
    if hints.is_action_request:
        return False
    if state.previous_result_reference.result_type:
        return True
    return fallback.topic in {"people", "knowledge", "conversation"} and fallback.confidence < 0.9


def _semantic_prompt(
    *,
    message: str,
    state: ConversationState,
    hints: ConversationHints,
    fallback: SemanticFrame,
) -> str:
    state_payload = {
        "active_domain": state.active_domain,
        "active_topic": state.active_topic,
        "active_object": state.active_object,
        "active_collection": state.active_collection,
        "previous_result": {
            "result_type": state.previous_result_reference.result_type,
            "collection_type": state.previous_result_reference.collection_type,
            "target_label": state.previous_result_reference.target_label,
            "count": state.previous_result_reference.count,
            "filters": state.previous_result_reference.filters,
            "field_projection": state.previous_result_reference.field_projection,
        },
        "pending_confirmation": bool(state.pending_confirmation.kind),
        "pending_clarification": bool(state.pending_clarification.kind),
    }
    return f"""Role: Conversation First Semantic Understanding. JSON only.
You only understand the current user message using conversation state.
Do not choose capability, provider, runtime, permission, credential, or policy.

Allowed output:
{{
  "speech_act": "ask|followup|request_action|confirm|cancel|answer",
  "topic": "people|knowledge|communication|conversation",
  "operation": "ask|count|list|field_lookup|company_profile|knowledge_query|action_request|followup|exists",
  "requested_output": "natural_text|numeric_only|short_answer|count|name_only|full_list|detail|sidepanel",
  "target": {{"kind": "person|organization_unit|collection|field|previous_result|unknown", "value": "", "reference": ""}},
  "parameters": {{"organization_unit": "", "person_name": "", "field": "", "filters": {{}}, "previous_result_target": ""}},
  "confidence": 0.0,
  "ambiguities": []
}}

Rules:
- If the message is short or referential and previous_result exists, prefer previous_result instead of treating it as a new query.
- If unsure between two concrete meanings, put the ambiguity in ambiguities instead of guessing.
- Normalize varied wording into requested_output; do not copy phrasing such as "只答数字" literally.
- For organization phrases like "有商务部这个部门吗", target.value should be "商务部".

ConversationState:
{json.dumps(state_payload, ensure_ascii=False, default=str)}

DeterministicFallback:
{json.dumps(fallback.payload(), ensure_ascii=False, default=str)}

UserMessage: {message}
"""


def _semantic_frame_from_payload(
    *,
    payload: dict[str, Any],
    message: str,
    state: ConversationState,
    fallback: SemanticFrame,
) -> SemanticFrame | None:
    speech_act = _enum(payload.get("speech_act"), {"ask", "followup", "request_action", "confirm", "cancel", "answer"}, fallback.speech_act)
    if speech_act == "confirm" and not state.pending_confirmation.kind and not state.pending_clarification.kind:
        speech_act = fallback.speech_act if fallback.speech_act != "confirm" else "ask"
    topic = _enum(payload.get("topic"), {"people", "knowledge", "communication", "conversation"}, fallback.topic)
    operation = _enum(
        payload.get("operation"),
        {"ask", "count", "list", "field_lookup", "company_profile", "knowledge_query", "action_request", "followup", "exists"},
        fallback.operation,
    )
    requested_output = _enum(
        payload.get("requested_output"),
        {"natural_text", "numeric_only", "short_answer", "count", "name_only", "full_list", "detail", "sidepanel"},
        fallback.requested_output,
    )
    raw_target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    target = {str(key): value for key, value in raw_target.items() if key in {"kind", "value", "reference", "field"} and value not in {None, ""}}
    raw_parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
    parameters = dict(fallback.parameters)
    for key in ("organization_unit", "person_name", "field", "previous_result_target"):
        value = str(raw_parameters.get(key) or "").strip()
        if value:
            parameters[key] = value
    if parameters.get("field"):
        parameters["field"] = _canonical_people_field(str(parameters.get("field") or ""))
    if target.get("field"):
        target["field"] = _canonical_people_field(str(target.get("field") or ""))
    if parameters.get("person_name"):
        parameters["person_name"] = _canonical_person_name(str(parameters.get("person_name") or ""))
    if isinstance(raw_parameters.get("filters"), dict):
        parameters["filters"] = raw_parameters["filters"]
    parameters["raw_message"] = message
    if state.previous_result_reference.collection_type and target.get("reference") == "previous_result":
        parameters.setdefault(
            "previous_result",
            {
                "result_type": state.previous_result_reference.result_type,
                "collection_type": state.previous_result_reference.collection_type,
                "count": state.previous_result_reference.count,
                "target_label": state.previous_result_reference.target_label,
                "filters": state.previous_result_reference.filters,
                "field_projection": state.previous_result_reference.field_projection,
            },
        )
        if state.previous_result_reference.target_label:
            parameters.setdefault("previous_result_target", state.previous_result_reference.target_label)
    confidence = _confidence_float(payload.get("confidence"), fallback.confidence)
    if confidence < 0.55:
        return None
    ambiguities = tuple(str(item).strip() for item in payload.get("ambiguities", []) if str(item).strip()) if isinstance(payload.get("ambiguities"), list) else fallback.ambiguities
    return SemanticFrame(
        speech_act=speech_act,
        topic=topic,
        target=target or fallback.target,
        operation=operation,
        requested_output=requested_output,
        parameters=parameters,
        confidence=confidence,
        ambiguities=ambiguities,
        source="llm_semantic_understanding_v1",
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
    if hints.scope_hint == "department" and _looks_like_organization_unit_hint(hints.target_hint):
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
    if operation in {"followup", "list", "action_request"} and state.previous_result_reference.collection_type:
        params["previous_result"] = {
            "result_type": state.previous_result_reference.result_type,
            "collection_type": state.previous_result_reference.collection_type,
            "count": state.previous_result_reference.count,
            "target_label": state.previous_result_reference.target_label,
            "filters": state.previous_result_reference.filters,
            "field_projection": state.previous_result_reference.field_projection,
        }
        if state.previous_result_reference.target_label:
            params["previous_result_target"] = state.previous_result_reference.target_label
        if state.previous_result_reference.filters and "filters" not in params:
            params["filters"] = _normalized_previous_filters(state.previous_result_reference.filters)
    current_object = state.active_object if state.previous_result_reference.object_type == "person" else {}
    if current_object and field and not person_name:
        current_name = str(current_object.get("name") or "").strip()
        if current_name:
            params["person_name"] = current_name
            person_name = current_name
    if state.previous_result_reference.field_projection and person_name:
        inherited = state.previous_result_reference.field_projection
        params["inherited_field_projection"] = "title" if inherited == "job_title" else inherited
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
        "职位": "title",
        "岗位": "title",
        "直属上级": "leader",
        "上级": "leader",
        "领导": "leader",
        "性别": "gender",
    }
    return aliases.get(target_hint, "")


def _canonical_people_field(value: str) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "phone": "mobile",
        "telephone": "mobile",
        "mobile": "mobile",
        "手机号": "mobile",
        "电话": "mobile",
        "号码": "mobile",
        "email": "email",
        "mail": "email",
        "邮箱": "email",
        "position": "title",
        "job_title": "title",
        "title": "title",
        "role": "title",
        "岗位": "title",
        "职位": "title",
        "leader": "leader",
        "manager": "leader",
        "supervisor": "leader",
        "直属上级": "leader",
        "上级": "leader",
        "领导": "leader",
        "gender": "gender",
        "sex": "gender",
        "性别": "gender",
    }
    return aliases.get(normalized, normalized)


def _canonical_person_name(value: str) -> str:
    text = str(value or "").strip("，,。.!！?？")
    text = re.split(r"(?:是|的|什么|岗位|职位|职务|领导|直属上级|上级|电话|手机号|号码|邮箱|性别)", text, maxsplit=1)[0]
    return text.strip()


def _gender_filter(target_hint: str) -> str:
    if target_hint == "male":
        return "male"
    if target_hint == "female":
        return "female"
    return ""


def _looks_like_organization_unit_hint(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text in {"这个", "那个", "这些", "那些", "谁", "是谁", "分别是谁"}:
        return False
    if bool(re.search(r"(事业部|部门|中心|团队|小组|组|部)$", text)):
        return True
    if re.fullmatch(r"[A-Za-z0-9]{1,20}", text):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]{1,8}", text) and text not in {"男生", "男性", "女生", "女性"}:
        return True
    return False


def _person_candidate(message: str) -> str:
    compact = re.sub(r"\s+", "", str(message or ""))
    compact = compact.strip("，,。.!！?？")
    has_field = any(token in compact for token in ("电话", "手机号", "号码", "邮箱", "职位", "岗位", "领导", "直属上级", "上级", "性别", "是男是女"))
    if compact in {"我是谁", "你是谁"}:
        return ""
    if any(token in compact for token in ("公司", "部门", "我们", "男生", "男性", "女生", "女性", "有谁")):
        return ""
    if compact.startswith(("他", "她")):
        return ""
    if not has_field and any(token in compact for token in ("多少", "几位", "几个")):
        return ""
    compact_for_name = re.sub(r"^(那|那么|还有)", "", compact)
    match = re.match(r"(?P<name>[\u4e00-\u9fff]{2,4})(?:的|是什么)?(?:电话|手机号|号码|邮箱|职位|岗位|领导|直属上级|上级|性别|是男是女).*", compact_for_name)
    if match:
        return match.group("name").removesuffix("的")
    if not any(token in compact for token in ("公司", "部门", "我们", "你", "我")):
        match = re.match(r"(?P<name>[\u4e00-\u9fff]{2,4})是谁$", compact)
        if match:
            return match.group("name")
    if compact.startswith(("那", "那么")):
        name = re.sub(r"^(那|那么)", "", compact)
        name = re.sub(r"(的)?(你有吗|有吗|有么|有没有|呢)$", "", name)
        return name if 2 <= len(name) <= 4 else ""
    match = re.match(r"(?P<name>[\u4e00-\u9fff]{2,4})呢$", compact)
    return match.group("name") if match else ""


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _enum(value: Any, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def _confidence_float(value: Any, fallback: float) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return fallback


def _running_tests() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))
