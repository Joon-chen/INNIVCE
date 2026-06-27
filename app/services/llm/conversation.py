from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.services.llm.gateway import LLMGateway
from app.services.llm.routing_policy import llm_route_for_task
from app.services.runtime_v5.response_classification import classify_response_request


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
    classification = classify_response_request(question=context.question, answer=context.fallback_answer, intent="smalltalk", result_type="smalltalk")
    if classification.fact_kind in {"time", "date", "permission"}:
        return context.fallback_answer
    if not settings.bot_llm_conversation_enabled:
        return context.fallback_answer
    prompt = conversation_prompt(context)
    try:
        reply = (_complete_conversation_with_deadline(prompt) or "").strip()
    except Exception:
        return context.fallback_answer
    if not valid_conversation_reply(reply=reply, fallback_answer=context.fallback_answer):
        return context.fallback_answer
    return reply[:1200]


def _complete_conversation_with_deadline(prompt: str) -> str | None:
    route = llm_route_for_task("conversation")
    timeout_seconds = max(0.5, float(route.latency_budget_ms or 800) / 1000.0)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="conversation-llm")
    future = executor.submit(lambda: LLMGateway().complete_task_text(prompt, task_type="conversation", temperature=0.4))
    try:
        return future.result(timeout=timeout_seconds)
    except TimeoutError:
        future.cancel()
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def conversation_prompt(context: ConversationLLMContext) -> str:
    return f"""你是 Digital Advisor 的 Conversation LLM，只负责自然对话表达。

沟通画像：
{context.profile_text or "默认专业、简洁"}

最近对话与交互信号：
{context.session_context or "无"}

用户消息：{context.question[:500]}
系统事实种子：
{context.fallback_answer[:800]}

表达原则：
- 像一个靠谱的企业助理自然说话，不要像系统模板。
- 优先回应用户当下情绪和上下文，再给必要事实。
- 不要主动列能力菜单；只有用户问“你能做什么”时再简要说明。
- 称呼和语气必须尊重沟通画像；如果画像说不要直呼其名，就不要叫姓名。
- 用户纠正称呼、语气、偏好时，先承认并自然调整，不要辩解。
- 系统事实种子只是事实来源，不是回复模板；请用自然语言重写。
- 不确定时可以轻描淡写说明，但不要反复说“这个问题不够确定”。
- 可以引导用户补充范围、对象、时间或动作确认，但不要把普通聊天变成表单。

你禁止：
- 主动读取或编造企业业务数据。
- 对未解析的图片、表情、附件编造具体内容或情绪；只能说明“我看到了有这类消息，但还不能可靠识别内容”。
- 把沟通画像当成权限或事实来源；它只影响表达方式。
- 把闲聊升级成业务动作。
- 承诺已经查询、创建、发送、审批或完成任何事项。
- 改变权限边界、执行身份或系统事实。
- 回避或篡改系统事实种子中的姓名、角色、当前时间、权限边界等确定性事实；但可以用更自然的方式表达。
- 输出未经系统事实种子支持的数量、名单、金额、风险结论。
- 反复机械列菜单，例如“查审批、任务、日程、邮件”；除非用户在问你能做什么。

请只输出给用户看的最终回复，1-3句，简洁自然，有上下文感。"""


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
    classification = classify_response_request(answer=fallback_answer, intent="smalltalk", result_type="smalltalk")
    if classification.fact_kind in {"time", "date", "permission"} and text != str(fallback_answer or "").strip():
        return False
    return True


def conversation_context_from_actor(actor: Any) -> tuple[str, str]:
    return (
        str(getattr(actor, "display_name", "") or "").strip(),
        str(getattr(actor, "role", "") or "员工").strip(),
    )
