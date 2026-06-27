from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.runtime_v5.context import load_result_context, load_session_context, save_session_context


MAX_CONVERSATION_TURNS = 8
CONTEXT_TURNS_FOR_LLM = 4
CONTEXT_PACK_MAX_CHARS = 1200
_REFERRAL_WORDS = (
    "他们",
    "她们",
    "其中的",
    "里面",
    "之中",
    "各自",
    "刚才",
    "上次",
    "之前",
    "前一个",
    "上一轮",
    "上一个",
    "上一条",
    "名单",
    "名字",
    "姓名",
    "列表",
    "那些",
    "这些",
    "那个",
    "这个",
    "那几",
    "这几",
    "分别",
    "具体",
    "详细",
    "展开",
)


@dataclass(frozen=True)
class ConversationTurn:
    user: str = ""
    assistant: str = ""
    route_path: str = ""
    route_label: str = ""
    message_type: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ConversationTurn":
        return cls(
            user=str(payload.get("user") or "").strip(),
            assistant=str(payload.get("assistant") or "").strip(),
            route_path=str(payload.get("route_path") or "").strip(),
            route_label=str(payload.get("route_label") or "").strip(),
            message_type=str(payload.get("message_type") or "").strip(),
        )

    def payload(self) -> dict[str, str]:
        return {
            "user": self.user[:500],
            "assistant": self.assistant[:800],
            "route_path": self.route_path,
            "route_label": self.route_label,
            "message_type": self.message_type,
        }


@dataclass(frozen=True)
class ConversationContext:
    chat_id: str = ""
    turns: tuple[ConversationTurn, ...] = ()
    last_result_type: str = ""
    last_result_count: int = 0
    last_result_answer: str = ""

    def render_for_llm(self) -> str:
        lines: list[str] = []
        for turn in self.turns[-CONTEXT_TURNS_FOR_LLM:]:
            user_text = turn.user
            if turn.message_type and turn.message_type != "text":
                user_text = user_text or f"[上一条是 {turn.message_type} 类型消息]"
            if user_text:
                lines.append(f"用户：{user_text[:240]}")
            if turn.assistant:
                lines.append(f"助手：{turn.assistant[:320]}")
        if self.last_result_type:
            lines.append(f"最近业务结果：{self.last_result_type}，数量 {self.last_result_count}。")
            if self.last_result_answer:
                lines.append(f"最近业务摘要：{self.last_result_answer[:320]}")
        return "\n".join(lines[-10:])


@dataclass(frozen=True)
class ConversationContextPack:
    text: str = ""
    purpose: str = "conversation"
    included_turn_count: int = 0
    result_included: bool = False
    has_non_text_turn: bool = False
    signal_count: int = 0
    truncated: bool = False

    def quality(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "chars": len(self.text),
            "included_turn_count": self.included_turn_count,
            "result_included": self.result_included,
            "has_non_text_turn": self.has_non_text_turn,
            "signal_count": self.signal_count,
            "truncated": self.truncated,
        }


def record_conversation_turn(
    *,
    chat_id: str | None,
    user_text: str,
    assistant_text: str,
    route_path: str | None = None,
    route_label: str | None = None,
    message_type: str | None = None,
) -> None:
    if not chat_id:
        return
    session_context = load_session_context(chat_id)
    turns = _load_turns(session_context)
    turns.append(
        ConversationTurn(
            user=str(user_text or "").strip(),
            assistant=str(assistant_text or "").strip(),
            route_path=str(route_path or "").strip(),
            route_label=str(route_label or "").strip(),
            message_type=str(message_type or "").strip(),
        )
    )
    session_context["conversation_turns"] = [turn.payload() for turn in turns[-MAX_CONVERSATION_TURNS:]]
    save_session_context(chat_id, session_context)


def load_conversation_context(chat_id: str | None) -> ConversationContext:
    if not chat_id:
        return ConversationContext()
    session_context = load_session_context(chat_id)
    result_context = load_result_context(chat_id)
    return ConversationContext(
        chat_id=chat_id,
        turns=tuple(_load_turns(session_context)),
        last_result_type=result_context.result_type if result_context else "",
        last_result_count=result_context.count if result_context else 0,
        last_result_answer=result_context.answer if result_context else "",
    )


def conversation_context_text(chat_id: str | None) -> str:
    return load_conversation_context(chat_id).render_for_llm()


def conversation_context_pack(
    chat_id: str | None,
    *,
    question: str = "",
    purpose: str = "conversation",
) -> ConversationContextPack:
    context = load_conversation_context(chat_id)
    return _build_context_pack(context=context, question=question, purpose=purpose)


def _build_context_pack(
    *,
    context: ConversationContext,
    question: str,
    purpose: str,
) -> ConversationContextPack:
    include_result = purpose == "conversation" or _question_references_previous(question)
    include_turns = purpose == "conversation" or include_result
    max_turns = CONTEXT_TURNS_FOR_LLM if include_turns else 0
    lines: list[str] = []
    selected_turns = context.turns[-max_turns:] if max_turns else ()
    signals = _conversation_signals(question=question, turns=selected_turns if purpose == "conversation" else ())
    if purpose == "conversation" and signals:
        lines.append("对话信号：" + "；".join(signals))
    has_non_text_turn = False
    for turn in selected_turns:
        user_text = turn.user
        if turn.message_type and turn.message_type != "text":
            has_non_text_turn = True
            user_text = user_text or f"[上一条是 {turn.message_type} 类型消息]"
        if user_text:
            lines.append(f"用户：{user_text[:200]}")
        if turn.assistant:
            lines.append(f"助手：{turn.assistant[:260]}")
    result_included = bool(include_result and context.last_result_type)
    if result_included:
        prefix = "可用最近业务结果" if purpose == "conversation" else "最近业务结果"
        lines.append(f"{prefix}：{context.last_result_type}，数量 {context.last_result_count}。")
        if context.last_result_answer:
            lines.append(f"最近业务摘要：{context.last_result_answer[:280]}")
    text = "\n".join(lines[-10:])
    truncated = len(text) > CONTEXT_PACK_MAX_CHARS
    if truncated:
        text = text[:CONTEXT_PACK_MAX_CHARS].rstrip()
    return ConversationContextPack(
        text=text,
        purpose=purpose,
        included_turn_count=len(selected_turns),
        result_included=result_included,
        has_non_text_turn=has_non_text_turn,
        signal_count=len(signals),
        truncated=truncated,
    )


def _question_references_previous(question: str) -> bool:
    text = str(question or "")
    return any(word in text for word in _REFERRAL_WORDS)


def _conversation_signals(*, question: str, turns: tuple[ConversationTurn, ...]) -> list[str]:
    texts = [str(question or "")]
    texts.extend(turn.user for turn in turns if turn.user)
    compact = " ".join(texts).replace(" ", "")
    signals: list[str] = []
    if _contains_any(compact, ("不要直呼我名字", "不要直接叫我名字", "不直接叫我名字", "别叫我名字")):
        signals.append("addressing_style=less_formal")
    if _contains_any(compact, ("叫我老板", "喊我老板", "我是你老板", "我是老板", "我是这个公司的老板")):
        signals.append("addressing_style=role_based")
    if _contains_any(compact, ("你忘了", "忘记了", "刚才告诉过你", "之前告诉过你", "不是告诉你")):
        signals.append("user_refers_to_prior_context=true")
    if _contains_any(compact, ("太机械", "太生硬", "笨", "没有情绪价值", "不像ai助理", "像机器人")):
        signals.append("tone=warmer_and_less_mechanical")
    if _contains_any(compact, ("别列菜单", "不要列菜单", "不要机械列菜单", "不要老列菜单", "别老列菜单")):
        signals.append("avoid_menu_repetition=true")
    return _dedupe(signals)[:6]


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _load_turns(session_context: dict[str, Any]) -> list[ConversationTurn]:
    raw_turns = session_context.get("conversation_turns") if isinstance(session_context, dict) else None
    if not isinstance(raw_turns, list):
        return []
    return [ConversationTurn.from_payload(item) for item in raw_turns if isinstance(item, dict)]
