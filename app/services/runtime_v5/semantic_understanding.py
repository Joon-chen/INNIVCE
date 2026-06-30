from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
import os
import re
from typing import Any

from app.core.config import settings
from app.services.llm.gateway import LLMGateway
from app.services.llm.routing_policy import llm_route_for_task
from app.services.runtime_v5.company_profile_query import looks_like_company_profile_query
from app.services.runtime_v5.conversation_state import ConversationState
from app.services.runtime_v5.semantic_fields import canonical_people_field
from app.services.semantic_protocol import SemanticFrame, validate_semantic_frame


@dataclass(frozen=True)
class DeterministicSignals:
    """Stable non-routing signals for Semantic Understanding.

    These signals are prompt/context inputs only. They are not allowed to pick a
    capability, provider, runtime, permission, or route.
    """

    domain_hint: str = ""
    operation_hint: str = ""
    requested_output_hint: str = ""
    target_hint: str = ""
    scope_hint: str = ""
    is_confirmation_word: bool = False
    is_cancel_word: bool = False
    is_followup_reference: bool = False
    is_action_request: bool = False
    keywords: tuple[str, ...] = ()
    text_compact: str = ""


def understand_semantics(
    *,
    message: str,
    state: ConversationState,
    signals: DeterministicSignals | None = None,
) -> SemanticFrame:
    """Build a SemanticFrame.

    Production can replace this deterministic V1 body with an LLM call, but the
    output contract must stay limited to semantic understanding.
    """

    signals = signals or build_deterministic_signals(message, state)
    frame = _deterministic_semantic_frame(message=message, state=state, hints=signals)
    return validate_semantic_frame(_llm_semantic_frame(message=message, state=state, hints=signals, fallback=frame) or frame)


_CONFIRMATION_WORDS = {"是", "是的", "对", "对的", "确认", "可以", "好的", "嗯"}
_CANCEL_WORDS = {"不用", "不用了", "取消", "算了", "先不用", "不要了"}
_FOLLOWUP_MARKERS = (
    "呢",
    "他",
    "她",
    "谁",
    "是谁",
    "分别是谁",
    "叫什么名字",
    "哪个",
    "哪些",
    "哪",
    "那个",
    "那位",
    "这个",
    "这些",
    "那些",
    "刚才",
    "上面",
    "下面",
    "下设",
    "子部门",
    "下级部门",
    "继续",
    "展开",
    "补全",
    "全部",
    "第",
)
_ACTION_MARKERS = ("发给", "发送", "发消息", "发信息", "发条信息", "发条消息", "发个信息", "发个消息", "发到", "通知", "拉群", "建群", "发邮件", "群发")
_MAIL_ACTION_MARKERS = ("发邮件", "写邮件", "写封邮件", "起草邮件", "草稿")
_MAIL_MARKERS = ("邮件", "收件箱", *_MAIL_ACTION_MARKERS)
_TASK_MARKERS = ("任务", "待办", "工作负荷", "工作安排")
_CALENDAR_MARKERS = ("日程", "会议", "开会", "安排")
_APPROVAL_MARKERS = ("审批", "待审批", "待我审批", "我发起的审批")
_IM_QUERY_MARKERS = ("群聊", "群列表", "多少群", "哪些群", "有哪些群", "当前群", "最近消息", "聊天记录")
_EXTERNAL_MARKERS = ("天气", "新闻", "附近", "打印店", "美国总统", "公开信息", "联网查", "网上查")
_ORGANIZATION_EXPORT_MARKERS = ("组织架构", "组织结构")
_KNOWLEDGE_MARKERS = (
    "公司是做什么",
    "主营业务",
    "业务介绍",
    "公司介绍",
    "公司有哪些产品",
    "公司的客户",
    "公司联系方式",
    "制度",
    "流程",
    "文档",
    "资料",
    "知识库",
    "云文档",
)
_PEOPLE_MARKERS = (
    "多少人",
    "男生",
    "男性",
    "女生",
    "女性",
    "电话",
    "手机号",
    "号码",
    "邮箱",
    "职位",
    "岗位",
    "通讯录",
    "名单",
    "人员",
    "人事",
    "人力资源",
    "HR",
    "hr",
    "行政",
    "财务",
    "IT",
    "it",
    "工程师",
    "部门",
    "董事长",
    "负责人",
    "经理",
    "主管",
    "总监",
    "专员",
    "下面",
    "下设",
    "子部门",
)


def build_deterministic_signals(message: str, state: ConversationState) -> DeterministicSignals:
    text = str(message or "").strip()
    compact = re.sub(r"\s+", "", text)
    keywords = _keywords(compact)
    action_request = _is_action_request_signal(compact)
    domain_hint = _domain_hint(compact=compact, state=state)
    return DeterministicSignals(
        domain_hint=domain_hint,
        operation_hint=_operation_hint(compact=compact, state=state),
        requested_output_hint=_requested_output_hint(compact=compact),
        target_hint=_target_hint(text=text, compact=compact),
        scope_hint=_scope_hint(compact=compact, state=state),
        is_confirmation_word=compact in _CONFIRMATION_WORDS,
        is_cancel_word=compact in _CANCEL_WORDS,
        is_followup_reference=_is_followup_reference(compact=compact, state=state),
        is_action_request=action_request,
        keywords=keywords,
        text_compact=compact,
    )


def _domain_hint(*, compact: str, state: ConversationState) -> str:
    if compact.startswith("/"):
        return "System"
    if looks_like_company_profile_query(compact):
        return "Knowledge"
    if _is_plain_conversation(compact):
        return "Conversation"
    if _is_non_work_life_request(compact):
        return "Conversation"
    if state.previous_result_reference.result_type and _is_unanchored_identity_question(compact):
        return "Conversation"
    if any(token in compact for token in ("不是任务", "不是日程", "不是邮件", "说的是别的")):
        return "Conversation"
    if compact.startswith(("你知道我", "我在这个公司", "我现在在这个公司")):
        return "Conversation"
    if any(marker in compact for marker in _EXTERNAL_MARKERS):
        return "External"
    if any(marker in compact for marker in _APPROVAL_MARKERS):
        return "Process"
    if _looks_like_organization_export(compact):
        return "People"
    if any(marker in compact for marker in _KNOWLEDGE_MARKERS):
        return "Knowledge"
    if _person_candidate(compact):
        return "People"
    if any(marker in compact for marker in _MAIL_ACTION_MARKERS):
        return "Mail"
    if any(marker in compact for marker in _ACTION_MARKERS):
        return "Communication"
    if any(marker in compact for marker in _IM_QUERY_MARKERS):
        return "Communication"
    if any(marker in compact for marker in _MAIL_MARKERS):
        return "Mail"
    if any(marker in compact for marker in _TASK_MARKERS):
        return "Task"
    if any(marker in compact for marker in _CALENDAR_MARKERS):
        return "Calendar"
    if any(token in compact for token in ("多少个人", "多少员工", "人员构成", "人员结构")):
        return "People"
    if _organization_unit_candidate(compact):
        return "People"
    if any(marker in compact for marker in _PEOPLE_MARKERS) or _looks_like_named_person_question(compact):
        return "People"
    if "部" in compact and any(token in compact for token in ("多少", "哪些", "有哪些", "人", "名单")):
        return "People"
    if _is_followup_reference(compact=compact, state=state) and state.active_domain:
        return state.active_domain
    return ""


def _is_action_request_signal(compact: str) -> bool:
    if any(token in compact for token in _ACTION_MARKERS):
        return True
    if any(token in compact for token in _MAIL_ACTION_MARKERS):
        return True
    if _looks_like_organization_export(compact):
        return True
    if any(token in compact for token in ("创建", "新建")) and any(token in compact for token in _CALENDAR_MARKERS):
        return True
    if "安排" in compact and any(token in compact for token in ("会议", "开会", "日程")):
        return True
    if any(token in compact for token in ("创建", "新建")) and any(token in compact for token in _TASK_MARKERS):
        return True
    return False


def _operation_hint(*, compact: str, state: ConversationState) -> str:
    if compact.startswith("/"):
        return "ask"
    if _is_plain_conversation(compact):
        return "ask"
    if _is_non_work_life_request(compact):
        return "ask"
    if any(token in compact for token in ("不是任务", "不是日程", "不是邮件", "说的是别的")):
        return "ask"
    if compact in _CANCEL_WORDS:
        return "cancel"
    if compact in _CONFIRMATION_WORDS:
        return "confirm"
    if any(token in compact for token in _ACTION_MARKERS):
        return "action_request"
    if any(token in compact for token in _MAIL_ACTION_MARKERS):
        return "action_request"
    if _looks_like_organization_export(compact):
        return "action_request"
    if any(token in compact for token in ("创建", "新建")) and any(token in compact for token in _CALENDAR_MARKERS):
        return "action_request"
    if "安排" in compact and any(token in compact for token in ("会议", "开会", "日程")):
        return "action_request"
    if any(token in compact for token in ("创建", "新建")) and any(token in compact for token in _TASK_MARKERS):
        return "action_request"
    if compact in {"继续", "展开", "补全", "全部显示", "全部展示", "全部名单", "全部列出"} and state.previous_result_reference.result_type:
        return "followup" if compact == "继续" else "list"
    if _is_previous_result_presentation_request(compact=compact, state=state):
        return "list"
    if looks_like_company_profile_query(compact):
        return "company_profile"
    if any(token in compact for token in ("制度", "流程", "文档", "资料", "知识库")):
        return "knowledge_query"
    if _organization_relation_hint(compact) == "children":
        return "list" if _asks_collection_identity(compact) else "count"
    if "通讯录" in compact and any(token in compact for token in ("发我", "发下", "给我", "给我下")):
        return "list"
    if _is_self_or_assistant_identity_question(compact):
        return "ask"
    if state.previous_result_reference.collection_type and _asks_collection_identity(compact):
        return "list"
    if any(token in compact for token in ("有谁的号码", "谁的号码", "有谁的电话", "谁的电话")):
        return "list"
    if any(token in compact for token in ("电话", "手机号", "号码", "邮箱", "职位", "岗位", "领导", "直属上级", "上级", "是男是女", "性别")):
        return "field_lookup"
    if _asks_collection_identity(compact) or any(token in compact for token in ("哪", "名单", "列出", "展开", "全部", "补全", "继续")):
        return "list"
    if any(token in compact for token in ("多少人", "多少", "几个人", "几位", "几个", "数量", "男生", "男性", "女生", "女性")):
        return "count"
    if state.previous_result_reference.collection_type and _is_followup_reference(compact=compact, state=state):
        return "followup"
    return "ask"


def _requested_output_hint(*, compact: str) -> str:
    if looks_like_company_profile_query(compact):
        return "natural_text"
    if "数字" in compact and any(token in compact for token in ("只", "仅", "就行", "即可", "回答", "答")):
        return "numeric_only"
    if any(token in compact for token in ("只回答", "只要", "不用详情", "不需要详情", "只说")):
        return "short_answer"
    if "通讯录" in compact and any(token in compact for token in ("发我", "发下", "给我", "给我下")):
        return "sidepanel"
    if any(token in compact for token in ("哪", "名单", "明细", "全部", "列出", "展示", "展开", "补全", "侧边栏")):
        return "sidepanel"
    if _asks_collection_identity(compact):
        return "name_only"
    if any(token in compact for token in ("多少", "几位", "几个", "数量")):
        return "count"
    return "natural_text"


def _target_hint(*, text: str, compact: str) -> str:
    if _looks_like_organization_export(compact):
        return "组织架构"
    if any(token in compact for token in ("邮件", "收件箱", "邮箱里")):
        return ""
    if _organization_relation_hint(compact) == "children" and compact.startswith(("下面", "下设", "子部门", "下级部门")):
        return ""
    organization_unit = _organization_unit_candidate(compact)
    if organization_unit:
        return organization_unit
    if any(token in compact for token in ("是男是女", "男还是女", "女还是男", "男性还是女性")):
        return "性别"
    if any(token in compact for token in ("男生", "男性")):
        return "male"
    if any(token in compact for token in ("女生", "女性")):
        return "female"
    role = _role_title_candidate(compact)
    if role:
        return role
    for field in ("电话", "手机号", "号码", "邮箱", "职位", "岗位", "直属上级", "上级", "领导", "负责人", "性别"):
        if field in compact:
            return field
    if _is_generic_organization_reference(compact):
        return ""
    return ""


def _role_title_candidate(compact: str) -> str:
    text = str(compact or "")
    if not any(token in text for token in ("董事长", "负责人", "工程师", "经理", "主管", "总监", "专员", "人力资源", "HR", "hr")):
        return ""
    candidate = text
    for token in ("公司", "全公司", "谁是", "是谁", "是哪位", "哪位", "哪个", "哪些", "有多少", "多少", "几个", "几位", "人员", "员工", "同事", "的"):
        candidate = candidate.replace(token, "")
    if 2 <= len(candidate) <= 20 and any(token in candidate for token in ("董事长", "负责人", "工程师", "经理", "主管", "总监", "专员")):
        return candidate
    return ""


def _looks_like_organization_export(compact: str) -> bool:
    if not any(marker in compact for marker in _ORGANIZATION_EXPORT_MARKERS):
        return False
    return any(token in compact for token in ("创建", "新建", "建一个", "建个", "放进去", "写入", "导出", "发给我", "发我"))


def _deterministic_semantic_frame(
    *,
    message: str,
    state: ConversationState,
    hints: DeterministicSignals,
) -> SemanticFrame:
    speech_act = _speech_act(hints=hints, state=state)
    topic = _topic(hints=hints, state=state)
    operation = _operation(hints=hints, speech_act=speech_act)
    parameters = _parameters(message=message, state=state, hints=hints, operation=operation)
    target = _target(hints=hints, parameters=parameters, state=state)
    if _is_inline_previous_result_presentation_request(message=message, state=state, target=target, parameters=parameters):
        speech_act = "followup"
        operation = "list"
        parameters["presentation_preference"] = "inline_text"
        target = {**target, "kind": "previous_result", "reference": "previous_result"}
        requested_output = "full_list"
    else:
        requested_output = hints.requested_output_hint or "natural_text"
    ambiguities = _ambiguities(hints=hints, state=state, operation=operation, parameters=parameters)
    return SemanticFrame(
        speech_act=speech_act,
        topic=topic,
        target=target,
        operation=operation,
        requested_output=requested_output,
        parameters=parameters,
        confidence=0.72 if ambiguities else _confidence(hints=hints, state=state),
        ambiguities=ambiguities,
    )


def _llm_semantic_frame(
    *,
    message: str,
    state: ConversationState,
    hints: DeterministicSignals,
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


def _should_try_llm_semantics(*, fallback: SemanticFrame, state: ConversationState, hints: DeterministicSignals) -> bool:
    if fallback.speech_act in {"confirm", "cancel"}:
        return False
    if hints.is_action_request:
        return False
    if fallback.topic == "conversation" and not hints.keywords and not hints.is_followup_reference:
        return False
    if state.previous_result_reference.result_type:
        return True
    return fallback.topic in {"people", "knowledge", "conversation"} and fallback.confidence < 0.9


def _semantic_prompt(
    *,
    message: str,
    state: ConversationState,
    hints: DeterministicSignals,
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
  "topic": "people|knowledge|communication|mail|task|calendar|conversation",
  "operation": "ask|count|list|field_lookup|company_profile|knowledge_query|action_request|followup|exists",
  "requested_output": "natural_text|numeric_only|short_answer|count|name_only|full_list|detail|sidepanel",
  "target": {{"kind": "person|organization_unit|collection|field|previous_result|unknown", "value": "", "reference": ""}},
  "parameters": {{"organization_unit": "", "person_name": "", "field": "", "organization_relation": "", "filters": {{}}, "previous_result_target": ""}},
  "confidence": 0.0,
  "ambiguities": []
}}

Rules:
- DeterministicFallback is only a weak hint. Correct it when the current message is a general conversation or common-knowledge question.
- If the message is short or referential and previous_result exists, prefer previous_result instead of treating it as a new query.
- If unsure between two concrete meanings, put the ambiguity in ambiguities instead of guessing.
- Normalize varied wording into requested_output; do not copy phrasing such as "只答数字" literally.
- For organization phrases like "有商务部这个部门吗", target.value should be "商务部".
- Use topic "people" only for enterprise people or organization facts, such as directory count/list, department members, or a person's phone/email/job title/manager/gender.
- A bare open question like "X是谁", "认识X吗", or a history/common-knowledge question is topic "conversation" unless the message or previous_result explicitly anchors X to the enterprise directory or organization.

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
    topic = _enum(payload.get("topic"), {"people", "knowledge", "communication", "mail", "task", "calendar", "conversation"}, fallback.topic)
    if _contradicts_explicit_topic_anchor(message=message, topic=topic, payload=payload, fallback=fallback):
        return None
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
    for key in ("organization_unit", "person_name", "field", "organization_relation", "previous_result_target"):
        value = str(raw_parameters.get(key) or "").strip()
        if value:
            parameters[key] = value
    if parameters.get("field"):
        parameters["field"] = canonical_people_field(str(parameters.get("field") or ""))
    if target.get("field"):
        target["field"] = canonical_people_field(str(target.get("field") or ""))
    if parameters.get("person_name"):
        parameters["person_name"] = _canonical_person_name(str(parameters.get("person_name") or ""))
    if isinstance(raw_parameters.get("filters"), dict):
        parameters["filters"] = raw_parameters["filters"]
    parameters["raw_message"] = message
    if state.previous_result_reference.collection_type and (
        target.get("reference") == "previous_result" or target.get("kind") == "previous_result"
    ):
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
        if state.previous_result_reference.filters and "filters" not in parameters:
            parameters["filters"] = _normalized_previous_filters(state.previous_result_reference.filters)
    if _is_inline_previous_result_presentation_request(message=message, state=state, target=target, parameters=parameters):
        speech_act = "followup"
        operation = "list"
        requested_output = "full_list"
        target = {**target, "kind": "previous_result", "reference": "previous_result"}
        parameters["presentation_preference"] = "inline_text"
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


def _contradicts_explicit_topic_anchor(
    *,
    message: str,
    topic: str,
    payload: dict[str, Any],
    fallback: SemanticFrame,
) -> bool:
    anchor = _explicit_topic_anchor(message)
    if not anchor:
        return False
    if topic == anchor:
        return False
    fallback_topic = str(fallback.topic or "")
    if fallback_topic != anchor:
        return False
    raw_target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    target_text = "".join(str(raw_target.get(key) or "") for key in ("kind", "value", "reference", "field"))
    return anchor in {"approval", "task", "calendar", "mail"} or _target_mentions_anchor(target_text, anchor)


def _is_inline_previous_result_presentation_request(
    *,
    message: str,
    state: ConversationState,
    target: dict[str, Any],
    parameters: dict[str, Any],
) -> bool:
    if not state.previous_result_reference.collection_type:
        return False
    if not isinstance(parameters.get("previous_result"), dict):
        return False
    if target.get("kind") != "previous_result" and target.get("reference") != "previous_result":
        return False
    compact = re.sub(r"\s+", "", str(message or ""))
    if not compact:
        return False
    wants_inline = any(token in compact for token in ("不要在侧边栏", "别在侧边栏", "不用侧边栏", "不要侧边栏", "对话框", "聊天框", "直接显示"))
    wants_result = any(token in compact for token in ("显示", "发出来", "列出来", "名单", "明细", "详情", "全部"))
    return wants_inline and wants_result


def _explicit_topic_anchor(message: str) -> str:
    compact = re.sub(r"\s+", "", str(message or ""))
    if not compact:
        return ""
    if "审批" in compact:
        return "approval"
    if any(token in compact for token in ("邮件", "收件箱")):
        return "mail"
    if any(token in compact for token in ("日程", "会议", "开会")):
        return "calendar"
    if any(token in compact for token in ("任务", "待办")):
        return "task"
    return ""


def _target_mentions_anchor(target_text: str, anchor: str) -> bool:
    if anchor == "approval":
        return "approval" in target_text or "审批" in target_text
    if anchor == "task":
        return "task" in target_text or "任务" in target_text or "待办" in target_text
    if anchor == "calendar":
        return "calendar" in target_text or "日程" in target_text or "会议" in target_text
    if anchor == "mail":
        return "mail" in target_text or "邮件" in target_text
    return False


def _speech_act(*, hints: DeterministicSignals, state: ConversationState) -> str:
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


def _topic(*, hints: DeterministicSignals, state: ConversationState) -> str:
    if hints.domain_hint == "System":
        return "system"
    if hints.domain_hint == "Conversation":
        return "conversation"
    if hints.domain_hint == "Process":
        return "approval"
    if hints.domain_hint == "External":
        return "external"
    if hints.domain_hint == "People":
        return "people"
    if hints.domain_hint == "Knowledge":
        return "knowledge"
    if hints.domain_hint == "Communication":
        return "communication"
    if hints.domain_hint == "Mail":
        return "mail"
    if hints.domain_hint == "Task":
        return "task"
    if hints.domain_hint == "Calendar":
        return "calendar"
    if state.active_topic:
        return state.active_topic
    return "conversation"


def _operation(*, hints: DeterministicSignals, speech_act: str) -> str:
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
    hints: DeterministicSignals,
    operation: str,
) -> dict[str, Any]:
    params: dict[str, Any] = {"raw_message": message}
    if hints.target_hint:
        params["target_hint"] = hints.target_hint
    if hints.scope_hint:
        params["scope_hint"] = hints.scope_hint
    if hints.scope_hint == "department" and _looks_like_organization_unit_hint(hints.target_hint):
        params["organization_unit"] = hints.target_hint
    relation = _organization_relation_hint(re.sub(r"\s+", "", str(message or "")))
    if relation:
        params["organization_relation"] = relation
    person_name = _person_candidate(message)
    if person_name:
        params["person_name"] = _canonical_person_name(person_name)
    field = _requested_field(hints.target_hint)
    if field:
        params["field"] = field
    elif relation == "leader":
        params["field"] = "leader"
    gender = _gender_filter(hints.target_hint)
    if gender:
        params["filters"] = {"gender": gender}
    if (
        hints.is_followup_reference
        or operation in {"followup", "list", "count", "field_lookup", "action_request"}
        or _is_previous_result_presentation_request(compact=re.sub(r"\s+", "", str(message or "")), state=state)
    ) and state.previous_result_reference.collection_type:
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
    hints: DeterministicSignals,
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
    if state.previous_result_reference.collection_type and (
        hints.is_followup_reference or _is_previous_result_presentation_request(compact=hints.text_compact, state=state)
    ):
        target["kind"] = "previous_result"
        target["reference"] = "previous_result"
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
    hints: DeterministicSignals,
    state: ConversationState,
    operation: str,
    parameters: dict[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    if hints.is_confirmation_word and not state.pending_confirmation.kind and not state.pending_clarification.kind:
        issues.append("confirmation_without_pending_state")
    raw_message = str(parameters.get("raw_message") or "")
    compact = re.sub(r"\s+", "", raw_message)
    if (
        operation == "field_lookup"
        and parameters.get("field")
        and not parameters.get("person_name")
        and not state.previous_result_reference.object_type
        and not _is_self_or_assistant_identity_question(compact)
    ):
        issues.append("missing_person_target")
    if (
        operation == "field_lookup"
        and any(token in compact for token in ("他", "她", "他的", "她的"))
        and state.previous_result_reference.result_type
        and state.previous_result_reference.object_type != "person"
    ):
        issues.append("missing_person_target")
    if hints.is_action_request and not state.previous_result_reference.has_items and not hints.target_hint:
        issues.append("missing_action_target")
    return tuple(issues)


def _confidence(*, hints: DeterministicSignals, state: ConversationState) -> float:
    if hints.is_followup_reference and state.active_domain:
        return 0.88
    if hints.domain_hint in {"People", "Knowledge"}:
        return 0.86
    if hints.is_action_request:
        return 0.78
    return 0.66


def _requested_field(target_hint: str) -> str:
    field = canonical_people_field(target_hint)
    return field if field in {"mobile", "email", "title", "leader", "gender"} else ""


def _canonical_person_name(value: str) -> str:
    text = str(value or "").strip("，,。.!！?？")
    text = re.sub(r"^(那就|那|把|帮我|请|请问|查一下|一下|查|告诉我)", "", text)
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


def _organization_unit_candidate(compact: str) -> str:
    matches = re.findall(
        r"(?:公司)?(?P<unit>[\u4e00-\u9fffA-Za-z0-9]{1,30}(?:事业部|部门|中心|团队|小组|组|部))"
        r"(?=(?:的|有|都|里|内|这|那|多少|几|哪些|有哪些|成员|人员|同事|名单|叫|名字|吗|$))",
        compact,
    )
    for raw in reversed(matches):
        unit = _clean_organization_unit(raw)
        if unit:
            return unit
    return _bare_organization_unit_candidate(compact)


def _bare_organization_unit_candidate(compact: str) -> str:
    patterns = (
        r"(?:我们公司|我们|公司)(?:的)?(?P<unit>[A-Za-z0-9]{1,20}|[\u4e00-\u9fff]{1,8})(?:是谁|有谁|有哪些|多少人|几人|几位|几个)(?:呀|啊|呢|吗)?$",
        r"(?:有多少个|有多少位|有多少|多少个|多少位|多少|有几个|几个|有几位|几位|有几人|几人)(?P<unit>[A-Za-z0-9]{1,20}|[\u4e00-\u9fff]{1,8})$",
        r"(?P<unit>[A-Za-z0-9]{1,20}|[\u4e00-\u9fff]{1,8})(?:有多少人|多少人|有几人|几人|有哪些人|有哪些|都有谁|是谁|名单)(?:呀|啊|呢|吗)?$",
    )
    for pattern in patterns:
        match = re.search(pattern, compact, re.I)
        if match:
            unit = _clean_bare_organization_unit(match.group("unit"))
            if unit:
                return unit
    return ""


def _clean_bare_organization_unit(value: str) -> str:
    unit = str(value or "").strip()
    unit = unit.removesuffix("了")
    unit = re.sub(r"^(的|有|几个|几位|几人|多少)", "", unit)
    unit = re.sub(r"(的人|的同事|人员|成员)$", "", unit)
    if "的" in unit:
        unit = unit.rsplit("的", 1)[-1]
    if not unit or "有" in unit:
        return ""
    blocked = {
        "我",
        "你",
        "他",
        "她",
        "谁",
        "分别",
        "全部",
        "公司",
        "这个公司",
        "那个公司",
        "人",
        "位",
        "个",
        "个人",
        "员工",
        "同事",
        "男生",
        "男性",
        "女生",
        "女性",
        "电话",
        "号码",
        "手机号",
        "邮箱",
        "邮件",
        "封邮件",
        "群",
        "群聊",
        "名字",
        "名单",
        "老板",
        "工程师",
    }
    if unit.lower() in blocked:
        return ""
    if unit.lower() in {"it", "hr"} or unit in {"行政", "财务", "人事", "研发", "商务", "销售", "运营", "市场", "技术"}:
        return unit
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", unit) and not re.search(r"(事业部|部门|中心|团队|小组|组|部)$", unit):
        return ""
    if re.search(r"(工程师|经理|主管|总监|专员|助理|实习生|技术员|负责人|董事长)$", unit):
        return ""
    return unit


def _clean_organization_unit(value: str) -> str:
    unit = str(value or "").strip()
    unit = re.split(r"(下面|下设|子部门|下级部门)", unit, maxsplit=1)[0]
    unit = re.sub(r"(这个部门|那个部门|这个组|那个组|这个人|那个人|的人|成员|同事|人员|叫什么|叫啥|名字).*$", "", unit)
    for prefix in ("我问的是", "问的是", "我说的是", "说的是", "查一下", "查看", "帮我查", "请问", "有", "那", "这个", "那个", "有这个", "公司"):
        while unit.startswith(prefix):
            unit = unit.removeprefix(prefix).strip()
    if unit.startswith(("多少", "几", "哪个", "哪些")) or unit in {"全部", "局部"}:
        return ""
    if unit in {"部", "组", "部门", "小组", "这个部", "这个组", "这个部门", "那个部", "那个组", "那个部门", "有这个部", "有这个组", "有这个部门"}:
        return ""
    return unit


def _is_generic_organization_reference(compact: str) -> bool:
    return any(token in compact for token in ("这个部门", "那个部门", "有这个部门", "这个组", "那个组", "有这个组"))


def _organization_relation_hint(compact: str) -> str:
    if any(token in compact for token in ("下面", "下设", "子部门", "下级部门")):
        return "children"
    if any(token in compact for token in ("负责人", "领导", "直属上级", "上级")):
        return "leader"
    return ""


def _scope_hint(*, compact: str, state: ConversationState) -> str:
    if any(token in compact for token in _MAIL_MARKERS):
        return "self"
    if any(token in compact for token in _EXTERNAL_MARKERS):
        return "external"
    if any(token in compact for token in ("需要我处理", "我的任务", "我处理的任务")):
        return "self"
    if any(token in compact for token in ("全公司", "公司任务", "公司日程", "企业")):
        return "company"
    if any(token in compact for token in ("部门任务", "部门日程")):
        return "department"
    if _organization_unit_candidate(compact):
        return "department"
    if any(token in compact for token in ("公司", "我们", "通讯录", "全员")) and any(token in compact for token in ("多少", "几位", "几个", "数量")):
        return "organization"
    if any(token in compact for token in ("部门", "组")) or ("部" in compact and any(token in compact for token in ("多少", "哪些", "哪些人", "有哪些", "人"))):
        return "department"
    if any(token in compact for token in ("公司", "通讯录", "全员")):
        return "organization"
    if state.previous_result_reference.collection_type and _is_followup_reference(compact=compact, state=state):
        return state.previous_result_reference.collection_type
    if state.previous_result_reference.result_type and _is_unanchored_identity_question(compact):
        return ""
    if _looks_like_named_person_question(compact):
        return "person"
    return ""


def _is_plain_conversation(compact: str) -> bool:
    if not compact:
        return False
    if any(marker in compact for marker in (*_PEOPLE_MARKERS, *_KNOWLEDGE_MARKERS, *_ACTION_MARKERS, *_MAIL_MARKERS, *_TASK_MARKERS, *_CALENDAR_MARKERS, *_APPROVAL_MARKERS, *_IM_QUERY_MARKERS, *_EXTERNAL_MARKERS)):
        return False
    if compact.startswith(("你好", "您好", "在吗", "你在吗", "谢谢", "多谢")):
        return True
    return compact in {"早", "早上好", "晚上好", "辛苦了"}


def _is_non_work_life_request(compact: str) -> bool:
    return any(token in compact for token in ("外卖", "点餐", "奶茶", "咖啡")) and any(
        token in compact for token in ("不是任务", "不是什么任务", "别当任务", "不要当任务")
    )


def _keywords(compact: str) -> tuple[str, ...]:
    words: list[str] = []
    for token in (*_PEOPLE_MARKERS, *_KNOWLEDGE_MARKERS, *_ACTION_MARKERS, *_MAIL_MARKERS, *_TASK_MARKERS, *_CALENDAR_MARKERS, *_APPROVAL_MARKERS, *_IM_QUERY_MARKERS, *_EXTERNAL_MARKERS):
        if token in compact and token not in words:
            words.append(token)
    return tuple(words)


def _is_followup_reference(*, compact: str, state: ConversationState) -> bool:
    if not state.active_domain and not state.previous_result_reference.result_type:
        return False
    if _is_unanchored_identity_question(compact):
        return False
    if _is_previous_result_presentation_request(compact=compact, state=state):
        return True
    if any(marker in compact for marker in _FOLLOWUP_MARKERS) or compact in _CONFIRMATION_WORDS:
        return True
    return state.previous_result_reference.collection_type != "" and _short_contextual_question(compact)


def _asks_collection_identity(compact: str) -> bool:
    return bool(re.search(r"(谁|叫什?么|叫啥|名字|名单|哪(?:些|个|几)|都有谁|分别)", compact))


def _short_contextual_question(compact: str) -> bool:
    if not compact or len(compact) > 12:
        return False
    if any(token in compact for token in ("任务", "审批", "邮件", "日程", "会议", "文档")):
        return False
    return bool(re.search(r"(谁|哪|什么|叫|名字|有吗|在吗|呢|第\d+|继续|展开|补全)", compact))


def _looks_like_named_person_question(compact: str) -> bool:
    if any(token in compact for token in ("公司", "部门", "我们", "你", "我")):
        return False
    if compact.startswith(("分别", "全部", "哪些", "哪个", "哪位")):
        return False
    return bool(re.search(r"^[\u4e00-\u9fff]{2,4}(的)?(电话|手机号|号码|邮箱|职位|岗位|领导|直属上级|上级|性别|是男是女|是谁)", compact))


def _is_self_or_assistant_identity_question(compact: str) -> bool:
    if compact in {"我是谁", "你是谁"} or compact.startswith(("我是谁", "你是谁")):
        return True
    if any(token in compact for token in ("我的", "我在", "我现在", "你知道我", "我是不是", "我是")) and any(
        token in compact for token in ("身份", "名字", "职位", "岗位", "老板", "称呼", "外号")
    ):
        return True
    return False


def _person_candidate(message: str) -> str:
    compact = re.sub(r"\s+", "", str(message or ""))
    compact = compact.strip("，,。.!！?？")
    has_field = any(token in compact for token in ("电话", "手机号", "号码", "邮箱", "职位", "岗位", "领导", "负责人", "直属上级", "上级", "性别", "是男是女"))
    if compact in {"我是谁", "你是谁"}:
        return ""
    if _organization_unit_candidate(compact):
        return ""
    if any(token in compact for token in ("邮件", "邮箱里", "收件箱")):
        return ""
    if any(token in compact for token in ("公司", "部门", "我们", "男生", "男性", "女生", "女性", "有谁")):
        return ""
    if compact.startswith(("他", "她")):
        return ""
    if not has_field and any(token in compact for token in ("多少", "几位", "几个")):
        return ""
    compact_for_name = re.sub(r"^(那|那么|还有)", "", compact)
    match = re.match(r"(?P<name>[\u4e00-\u9fff]{2,4})(?:的|是什么)?(?:电话|手机号|号码|邮箱|职位|岗位|领导|负责人|直属上级|上级|性别|是男是女).*", compact_for_name)
    if match:
        return match.group("name").removesuffix("的")
    if any(token in compact for token in ("问的是他", "问的是她", "他的", "她的")):
        return ""
    match = re.search(r"(?P<name>[\u4e00-\u9fff]{2,4})的(?:电话|手机号|号码|邮箱|职位|岗位|领导|负责人|直属上级|上级|性别|是男是女)", compact_for_name)
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


def _is_unanchored_identity_question(compact: str) -> bool:
    if not compact:
        return False
    if any(token in compact for token in ("公司", "通讯录", "部门", "同事", "员工", "人员", "我们", "分别")):
        return False
    if compact.startswith(("哪", "哪些", "哪个", "哪位")):
        return False
    if any(token in compact for token in ("电话", "手机号", "号码", "邮箱", "职位", "岗位", "直属上级", "上级", "负责人", "董事长", "性别", "是男是女")):
        return False
    if compact in {"我是谁", "你是谁"}:
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]{2,16}是谁", compact))


def _is_previous_result_presentation_request(*, compact: str, state: ConversationState) -> bool:
    if not state.previous_result_reference.collection_type:
        return False
    if not compact:
        return False
    presentation_terms = ("明细", "详情", "全部", "显示", "展示", "列出", "列出来", "发出来", "直接发")
    inline_terms = ("侧边栏", "对话框", "聊天框")
    return any(token in compact for token in presentation_terms) or any(token in compact for token in inline_terms)


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
