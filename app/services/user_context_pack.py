from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.services.profile_context import (
    intent_profile_text,
    load_profile_context,
    presentation_profile_text,
)


class _ConversationPack(Protocol):
    text: str
    result_included: bool

    def quality(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class UserContextPack:
    """LLM-facing user context assembled from stable contracts.

    This is not a new engine. It is a compact input package for Command and
    presentation prompts so facts, profile, conversation, and result context do
    not drift across different callers.
    """

    identity_facts: dict[str, Any] = field(default_factory=dict)
    intent_profile_text: str = ""
    expression_profile_text: str = ""
    conversation_text: str = ""
    conversation_quality: dict[str, Any] = field(default_factory=dict)
    result_context_available: bool = False
    references_previous: bool = False

    def prompt_sections(self) -> str:
        return "\n".join(
            (
                "Facts:",
                _render_facts(self.identity_facts),
                "",
                "Intent Profile:",
                self.intent_profile_text or "无",
                "",
                "Expression Profile:",
                self.expression_profile_text or "无",
                "",
                "Context:",
                f"references_previous={'yes' if self.references_previous else 'no'}; result_context_available={'yes' if self.result_context_available else 'no'}",
                f"context_quality={self.conversation_quality}",
                "",
                "Conversation Context:",
                self.conversation_text or "无",
            )
        )


def build_user_context_pack(
    *,
    runtime_context: Any,
    question: str,
    purpose: str = "command",
) -> UserContextPack:
    from app.services.conversation_context import conversation_context_pack

    profile_context = load_profile_context(runtime_context=runtime_context)
    conversation_pack = conversation_context_pack(
        getattr(runtime_context, "chat_id", None),
        question=question,
        purpose=purpose,
    )
    return UserContextPack(
        identity_facts=_identity_facts(runtime_context),
        intent_profile_text=intent_profile_text(profile_context),
        expression_profile_text=presentation_profile_text(profile_context),
        conversation_text=conversation_pack.text,
        conversation_quality=conversation_pack.quality(),
        result_context_available=getattr(runtime_context, "result_context", None) is not None,
        references_previous=_references_previous(question=question, conversation_pack=conversation_pack),
    )


def _identity_facts(runtime_context: Any) -> dict[str, Any]:
    identity = getattr(runtime_context, "identity", None)
    scope = getattr(runtime_context, "runtime_scope", None)
    department_names = tuple(getattr(identity, "department_names", ()) or ()) if identity is not None else ()
    return {
        "assistant_name": "大飞哥 / Digital Advisor",
        "actor_open_id": str(getattr(identity, "open_id", "") or ""),
        "actor_display_name": str(getattr(identity, "display_name", "") or ""),
        "actor_role": str(getattr(identity, "role", "") or ""),
        "actor_job_title": str(getattr(identity, "job_title", "") or ""),
        "actor_departments": "、".join(str(item) for item in department_names[:3] if str(item)),
        "domains": list(getattr(identity, "domains", ()) or ()) if identity is not None else [],
        "active_company_id": str(getattr(scope, "active_company_id", "") or ""),
    }


def _references_previous(*, question: str, conversation_pack: _ConversationPack) -> bool:
    if bool(getattr(conversation_pack, "result_included", False)):
        return True
    text = str(question or "")
    return any(token in text for token in ("这个", "那个", "这些", "那些", "刚才", "上面", "继续", "展开", "第一个", "第二个"))


def _render_facts(facts: dict[str, Any]) -> str:
    lines: list[str] = []
    for key in (
        "assistant_name",
        "actor_open_id",
        "actor_display_name",
        "actor_role",
        "actor_job_title",
        "actor_departments",
        "domains",
        "active_company_id",
    ):
        value = facts.get(key)
        if value in (None, "", [], ()):
            continue
        lines.append(f"{key}={value}")
    return "\n".join(lines) or "无"
