from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.services.llm.gateway import LLMGateway


@dataclass(frozen=True)
class ConversationLLMContext:
    question: str
    fallback_answer: str
    actor_name: str = ""
    actor_role: str = ""
    profile_text: str = ""
    session_context: str = ""


def conversation_llm_reply(context: ConversationLLMContext) -> str:
    """Generate a conversational reply without reading or changing business data."""
    if not settings.bot_llm_conversation_enabled:
        return context.fallback_answer
    prompt = conversation_prompt(context)
    try:
        reply = (LLMGateway().complete_text(prompt, temperature=0.4) or "").strip()
    except Exception:
        return context.fallback_answer
    if not valid_conversation_reply(reply=reply, fallback_answer=context.fallback_answer):
        return context.fallback_answer
    return reply[:1200]


def conversation_prompt(context: ConversationLLMContext) -> str:
    capabilities = "审批、任务、日程、邮件、会议、通讯录、知识、企业认知聚合"
    return f"""你是 Digital Advisor 的 Conversation LLM，只负责闲聊、解释边界和引导补充信息。

用户：{context.actor_name or "当前用户"}（{context.actor_role or "员工"}）
用户画像：{context.profile_text or "默认专业、简洁"}
对话上下文：{context.session_context or "无"}

用户消息：{context.question[:500]}
系统原始回复：{context.fallback_answer[:800]}

你可以：
- 自然回应问候、感谢、抱怨或普通追问。
- 解释你能做什么：{capabilities}。
- 引导用户补充范围、对象、时间或动作确认。
- 说明权限、授权、未接入能力的边界。

你禁止：
- 主动读取或编造企业业务数据。
- 把闲聊升级成业务动作。
- 承诺已经查询、创建、发送、审批或完成任何事项。
- 改变权限边界、执行身份或系统原始事实。
- 输出未经系统原始回复支持的数量、名单、金额、风险结论。

请输出给用户看的最终回复，简洁自然。"""


def valid_conversation_reply(*, reply: str, fallback_answer: str) -> bool:
    text = str(reply or "").strip()
    if not text:
        return False
    if len(text) > max(len(fallback_answer or "") * 3, 1200):
        return False
    forbidden_claims = (
        "我已查询",
        "已查询到",
        "我已创建",
        "我已发送",
        "我已审批",
        "已完成审批",
        "我已经完成",
    )
    if any(claim in text for claim in forbidden_claims):
        return False
    return True


def conversation_context_from_actor(actor: Any) -> tuple[str, str]:
    return (
        str(getattr(actor, "display_name", "") or "").strip(),
        str(getattr(actor, "role", "") or "员工").strip(),
    )
