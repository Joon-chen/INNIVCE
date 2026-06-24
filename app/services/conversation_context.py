from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.runtime_v5.context import load_result_context, load_session_context, save_session_context


MAX_CONVERSATION_TURNS = 8
CONTEXT_TURNS_FOR_LLM = 4


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


def _load_turns(session_context: dict[str, Any]) -> list[ConversationTurn]:
    raw_turns = session_context.get("conversation_turns") if isinstance(session_context, dict) else None
    if not isinstance(raw_turns, list):
        return []
    return [ConversationTurn.from_payload(item) for item in raw_turns if isinstance(item, dict)]
