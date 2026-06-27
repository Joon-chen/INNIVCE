from __future__ import annotations

from dataclasses import dataclass
import re

from app.core.config import settings
from app.services.llm.gateway import LLMGateway


@dataclass(frozen=True)
class PresentationLLMContext:
    question: str
    original_answer: str
    profile_text: str = ""
    session_context: str = ""
    scope_label: str = ""


def presentation_llm_rewrite(context: PresentationLLMContext) -> str:
    """Rewrite answer wording without changing facts, counts, status, or permissions."""
    prompt = presentation_prompt(context)
    try:
        rewritten = (LLMGateway().complete_task_text(prompt, task_type="presentation", temperature=0.3) or "").strip()
    except Exception:
        return context.original_answer
    if not valid_presentation_rewrite(
        original=context.original_answer,
        rewritten=rewritten,
    ):
        return context.original_answer
    return rewritten[:3500]


def presentation_prompt(context: PresentationLLMContext) -> str:
    return f"""你是 Digital Advisor 的 Presentation LLM，只负责输出侧表达优化。

硬性边界：
- 保持原答案中的数量、状态、权限边界、风险等级、建议含义不变。
- 不得新增原答案或会话上下文中没有的信息。
- 不得把未接入、无权限、分析中、未查询到改写成已完成或已查询。
- 不得生成新的业务动作、确认、Provider、Tool 或执行身份。

用户画像：{context.profile_text or "默认专业、简洁"}
{context.session_context}
回答范围：{context.scope_label}
用户问题：{context.question[:500]}

原答案：
{context.original_answer[:settings.bot_llm_answer_rewrite_max_chars]}

输出改写后的答案："""


def valid_presentation_rewrite(*, original: str, rewritten: str) -> bool:
    if not rewritten:
        return False
    if not _presentation_scaffold_ok(rewritten):
        return False
    if len(rewritten) > max(len(original) * 2, 1200):
        return False
    if not _count_integrity_ok(original=original, rewritten=rewritten):
        return False
    if not _status_boundary_ok(original=original, rewritten=rewritten):
        return False
    return True


def _presentation_scaffold_ok(rewritten: str) -> bool:
    forbidden = (
        "改写后的答案",
        "以下是改写",
        "收到你的要求",
        "原答案",
        "输出改写",
    )
    return not any(text in rewritten for text in forbidden)


def _count_integrity_ok(*, original: str, rewritten: str) -> bool:
    orig_num = re.search(r"(?:共\s*)?(\d+)\s*(?:人|条|个|笔)", original)
    orig_alt = re.findall(r"(\d+)\s*[个条笔]", original)
    new_num = re.search(r"(\d+)\s*(?:名|条|个|笔)", rewritten)
    if orig_num and new_num and orig_num.group(1) != new_num.group(1):
        return False
    new_num2 = re.search(r"共有(\d+)", rewritten)
    if orig_num and not new_num and new_num2 and orig_num.group(1) != new_num2.group(1):
        return False
    new_all = re.findall(r"(\d+)\s*(?:名|条|个|笔)", rewritten)
    if orig_alt and new_all:
        orig_counts = set(orig_alt)
        new_counts = set(new_all)
        if not orig_counts.intersection(new_counts) and len(orig_counts) == 1:
            return False
    return True


def _status_boundary_ok(*, original: str, rewritten: str) -> bool:
    guarded_statuses = (
        "未接入",
        "没有接入",
        "没有权限",
        "无权限",
        "分析中",
        "未查询到",
        "暂未查询到",
        "需要授权",
        "等待确认",
    )
    completion_claims = (
        "已接入",
        "已查询到",
        "已完成",
        "可以查看全部",
        "已经授权",
        "已经确认",
    )
    if any(status in original for status in guarded_statuses):
        if any(claim in rewritten for claim in completion_claims):
            return False
    return True
