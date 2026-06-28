from __future__ import annotations

from dataclasses import dataclass, field
import re

from app.services.runtime_v5.conversation_state import ConversationState


@dataclass(frozen=True)
class ConversationHints:
    """Deterministic signals for Conversation First understanding.

    Hints are not routing decisions. They only make stable, auditable facts
    available to Semantic Understanding and DialogueResolver.
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
    "继续",
    "展开",
    "补全",
    "全部",
    "第",
)
_ACTION_MARKERS = ("发给", "发送", "发消息", "发到", "通知", "拉群", "建群", "发邮件", "群发")
_KNOWLEDGE_MARKERS = (
    "公司是做什么",
    "主营业务",
    "业务介绍",
    "公司介绍",
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
    "工程师",
    "部门",
    "董事长",
    "负责人",
)


def build_conversation_hints(message: str, state: ConversationState) -> ConversationHints:
    text = str(message or "").strip()
    compact = re.sub(r"\s+", "", text)
    keywords = _keywords(compact)
    domain_hint = _domain_hint(compact=compact, state=state)
    return ConversationHints(
        domain_hint=domain_hint,
        operation_hint=_operation_hint(compact=compact, state=state),
        requested_output_hint=_requested_output_hint(compact=compact),
        target_hint=_target_hint(text=text, compact=compact),
        scope_hint=_scope_hint(compact=compact, state=state),
        is_confirmation_word=compact in _CONFIRMATION_WORDS,
        is_cancel_word=compact in _CANCEL_WORDS,
        is_followup_reference=_is_followup_reference(compact=compact, state=state),
        is_action_request=any(marker in compact for marker in _ACTION_MARKERS),
        keywords=keywords,
        text_compact=compact,
    )


def _domain_hint(*, compact: str, state: ConversationState) -> str:
    if compact.startswith(("你知道我", "我在这个公司", "我现在在这个公司")):
        return "Conversation"
    if any(marker in compact for marker in _KNOWLEDGE_MARKERS):
        return "Knowledge"
    if any(marker in compact for marker in _ACTION_MARKERS):
        return "Communication"
    if any(marker in compact for marker in _PEOPLE_MARKERS) or _looks_like_named_person_question(compact):
        return "People"
    if "部" in compact and any(token in compact for token in ("多少", "哪些", "有哪些", "人", "名单")):
        return "People"
    if _is_followup_reference(compact=compact, state=state) and state.active_domain:
        return state.active_domain
    return ""


def _operation_hint(*, compact: str, state: ConversationState) -> str:
    if compact in _CANCEL_WORDS:
        return "cancel"
    if compact in _CONFIRMATION_WORDS:
        return "confirm"
    if any(token in compact for token in _ACTION_MARKERS):
        return "action_request"
    if compact in {"继续", "展开", "补全", "全部显示", "全部展示", "全部名单", "全部列出"} and state.previous_result_reference.result_type:
        return "followup" if compact == "继续" else "list"
    if any(token in compact for token in ("公司是做什么", "主营业务", "公司介绍")):
        return "company_profile"
    if any(token in compact for token in ("制度", "流程", "文档", "资料", "知识库")):
        return "knowledge_query"
    if "通讯录" in compact and any(token in compact for token in ("发我", "发下", "给我", "给我下")):
        return "list"
    if _is_self_or_assistant_identity_question(compact):
        return "ask"
    if state.previous_result_reference.collection_type and _asks_collection_identity(compact):
        return "list"
    if any(token in compact for token in ("电话", "手机号", "号码", "邮箱", "职位", "岗位", "是男是女", "性别")):
        return "field_lookup"
    if _asks_collection_identity(compact) or any(token in compact for token in ("哪", "名单", "列出", "展开", "全部", "补全", "继续")):
        return "list"
    if any(token in compact for token in ("多少人", "多少", "几个人", "几位", "数量", "男生", "男性", "女生", "女性")):
        return "count"
    if state.previous_result_reference.collection_type and _is_followup_reference(compact=compact, state=state):
        return "followup"
    return "ask"


def _requested_output_hint(*, compact: str) -> str:
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
    if any(token in compact for token in ("多少", "几位", "数量")):
        return "count"
    return "natural_text"


def _target_hint(*, text: str, compact: str) -> str:
    organization_unit = _organization_unit_candidate(compact)
    if organization_unit:
        return organization_unit
    if any(token in compact for token in ("是男是女", "男还是女", "女还是男", "男性还是女性")):
        return "性别"
    if any(token in compact for token in ("男生", "男性")):
        return "male"
    if any(token in compact for token in ("女生", "女性")):
        return "female"
    for field in ("电话", "手机号", "号码", "邮箱", "职位", "岗位", "性别"):
        if field in compact:
            return field
    if _is_generic_organization_reference(compact):
        return ""
    return ""


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
    return ""


def _clean_organization_unit(value: str) -> str:
    unit = str(value or "").strip()
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


def _scope_hint(*, compact: str, state: ConversationState) -> str:
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
    if _looks_like_named_person_question(compact):
        return "person"
    return ""


def _keywords(compact: str) -> tuple[str, ...]:
    words: list[str] = []
    for token in (*_PEOPLE_MARKERS, *_KNOWLEDGE_MARKERS, *_ACTION_MARKERS):
        if token in compact and token not in words:
            words.append(token)
    return tuple(words)


def _is_followup_reference(*, compact: str, state: ConversationState) -> bool:
    if not state.active_domain and not state.previous_result_reference.result_type:
        return False
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
    return bool(re.search(r"(谁|哪|什么|叫|名字|有吗|在吗|呢|第\\d+|继续|展开|补全)", compact))


def _looks_like_named_person_question(compact: str) -> bool:
    if any(token in compact for token in ("公司", "部门", "我们", "你", "我")):
        return False
    return bool(re.search(r"^[\u4e00-\u9fff]{2,4}(的)?(电话|手机号|号码|邮箱|职位|岗位|性别|是男是女|是谁)", compact))


def _is_self_or_assistant_identity_question(compact: str) -> bool:
    return compact in {"我是谁", "你是谁"} or compact.startswith(("我是谁", "你是谁"))
