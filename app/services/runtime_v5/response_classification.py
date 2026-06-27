from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ResponseClass = Literal["fact", "conversation", "query", "action", "reasoning", "clarify"]
FactKind = Literal["identity", "assistant_identity", "time", "date", "emoji", "permission", "unknown"]


@dataclass(frozen=True)
class ResponseClassification:
    response_class: ResponseClass
    fact_kind: FactKind = "unknown"
    llm_allowed: bool = False
    reason: str = ""


_CONVERSATION_CONTEXT_MARKERS = (
    "我是谁",
    "我叫什么",
    "我的名字",
    "我叫",
    "你知道我是谁",
    "你知道我叫什么",
    "你知道我吗",
    "你认识我吗",
    "我是老板吗",
    "我的身份",
    "我是什么身份",
    "我的职位",
    "我的岗位",
    "我在公司的职位",
    "我在这个公司的职位",
    "你知道我的职位",
    "你知道我的岗位",
    "你应该叫我什么",
    "你该叫我什么",
    "你到底叫我什么",
    "怎么称呼我",
    "如何称呼我",
    "我是什么性格",
    "我的性格",
    "我是老板",
    "我是老板吗",
    "我就是老板",
    "这个公司老板是谁",
    "这个公司的老板是谁",
    "当前公司老板是谁",
    "当前公司的老板是谁",
    "公司老板是谁",
    "公司的老板是谁",
    "之前告诉过你",
    "刚才告诉过你",
    "不是告诉你",
)
_ASSISTANT_IDENTITY_MARKERS = ("你是谁", "你叫什么", "你叫什么名字", "你是大飞哥", "你叫大飞哥")
_TIME_FACT_MARKERS = ("现在几点", "几点了")
_DATE_FACT_MARKERS = ("今天几号", "今天日期", "今天星期几")
_EMOJI_FACT_MARKERS = ("这个表情", "表情是什么", "什么情绪", "这个情绪", "这个emoji", "这个emoj")
_PERMISSION_FACT_MARKERS = ("未接入", "没有接入", "没有权限", "不能生成", "不会改用", "权限拦截", "需要授权")


def classify_response_request(*, question: str = "", answer: str = "", intent: str = "", result_type: str = "") -> ResponseClassification:
    """Classify the response before any LLM presentation layer can rewrite it.

    The class is about the nature of the answer, not the business domain. Fact
    responses are system-owned and must remain deterministic.
    """

    compact_question = _compact(question)
    compact_answer = _compact(answer)
    if (
        intent == "smalltalk"
        and (
            _contains_any(compact_question, _CONVERSATION_CONTEXT_MARKERS)
            or _is_self_role_question(compact_question)
            or _is_address_preference_question(compact_question)
            or _is_owner_statement(compact_question)
            or _identity_fact_answer(compact_answer)
        )
    ):
        return ResponseClassification("conversation", llm_allowed=True, reason="conversation_context")
    if _contains_any(compact_question, _ASSISTANT_IDENTITY_MARKERS) or "我是digitaladvisor" in compact_answer.lower():
        return ResponseClassification("fact", fact_kind="assistant_identity", llm_allowed=False, reason="assistant_identity_fact")
    if _contains_any(compact_question, _TIME_FACT_MARKERS) or "现在是北京时间" in answer:
        return ResponseClassification("fact", fact_kind="time", llm_allowed=False, reason="time_fact")
    if _contains_any(compact_question, _DATE_FACT_MARKERS) or compact_answer.startswith("今天是"):
        return ResponseClassification("fact", fact_kind="date", llm_allowed=False, reason="date_fact")
    if _contains_any(compact_question, _EMOJI_FACT_MARKERS) or "不能可靠识别具体表情" in answer:
        return ResponseClassification("fact", fact_kind="emoji", llm_allowed=False, reason="emoji_fact")
    if _contains_any(answer, _PERMISSION_FACT_MARKERS):
        return ResponseClassification("fact", fact_kind="permission", llm_allowed=False, reason="permission_boundary_fact")
    if intent == "smalltalk" or result_type == "smalltalk":
        return ResponseClassification("conversation", llm_allowed=True, reason="smalltalk_conversation")
    if intent.endswith("_create") or intent.endswith("_complete") or result_type.startswith("runtime_action"):
        return ResponseClassification("action", llm_allowed=False, reason="action_feedback")
    if result_type.endswith("_list") or result_type.endswith("_detail"):
        return ResponseClassification("query", llm_allowed=False, reason="query_result")
    return ResponseClassification("query", llm_allowed=False, reason="default_runtime_result")


def deterministic_fact_answer(answer: str) -> bool:
    return classify_response_request(answer=answer).response_class == "fact"


def _compact(text: str) -> str:
    return str(text or "").replace(" ", "").strip()


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _identity_fact_answer(compact_answer: str) -> bool:
    return (
        compact_answer.startswith("我知道，你是")
        or compact_answer.startswith("你叫")
        or compact_answer.startswith("你是")
        or compact_answer.startswith("是，你是")
        or compact_answer.startswith("平时我叫你")
        or "你是当前飞书会话里的用户" in compact_answer
        or "当前识别权限" in compact_answer
        or "系统所有者" in compact_answer
    )


def _is_self_role_question(compact_question: str) -> bool:
    return any(token in compact_question for token in ("职位", "岗位")) and any(
        token in compact_question for token in ("我", "我的", "我现在", "我在公司", "我在这个公司", "你知道我")
    )


def _is_address_preference_question(compact_question: str) -> bool:
    return any(
        token in compact_question
        for token in (
            "叫我",
            "喊我",
            "称呼我",
            "你叫我",
            "你喊我",
            "该叫我",
            "应该叫我",
            "到底叫我",
            "别叫我名字",
            "不要叫我名字",
            "不要直呼我名字",
            "不要直接叫我名字",
            "不直接叫我名字",
        )
    )


def _is_owner_statement(compact_question: str) -> bool:
    return "老板" in compact_question and any(token in compact_question for token in ("我是", "我就是", "我是不是", "你忘", "忘记", "告诉过你"))
