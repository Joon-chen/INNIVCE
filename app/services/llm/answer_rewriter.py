import os
from types import SimpleNamespace
from typing import Any

from app.core.config import settings
from app.services.conversation_context import conversation_context_pack, conversation_context_text
from app.services.llm.conversation import ConversationLLMContext, conversation_context_from_actor, conversation_llm_reply
from app.services.llm.presentation import PresentationLLMContext, presentation_llm_rewrite, valid_presentation_rewrite
from app.services.profile_context import (
    apply_profile_update,
    load_profile_context,
    load_profile_payload,
    presentation_profile_text,
)

# ── User personality profiles ───────────────────────────────────


def _profile_key(open_id: str) -> str:
    return f"feishu:profile:{open_id}"


def _get_user_profile(open_id: str) -> dict:
    return load_profile_payload(open_id)


def _profile_text(open_id: str, actor_style: str, actor: Any | None = None) -> str:
    """Return a short profile description for the LLM prompt."""
    runtime_context = SimpleNamespace(identity=actor) if actor is not None else None
    profile_context = load_profile_context(open_id=open_id, actor_style=actor_style, runtime_context=runtime_context)
    lines = [presentation_profile_text(profile_context)]
    fact_hint = _profile_fact_hint(actor)
    if fact_hint:
        lines.append(f"事实上下文：{fact_hint}；仅用于称呼和表达贴合，不作为权限依据")
    return "\n".join(lines)


def _get_session_context(chat_id: str | None, question: str = "") -> str:
    """Load recent V5 conversation context for conversational continuity."""
    if not chat_id:
        return ""
    runtime_context = _runtime_conversation_context(chat_id)
    if runtime_context:
        return runtime_context
    try:
        from app.services.llm.answer_semantics import _load_session
        session = _load_session(chat_id)
        if not session or not session.get("question"):
            return ""
        ctx = f"上一条对话：{session['question']}"
        # Only inject result context if question explicitly references previous answer
        _referral_words = {"他们", "她们", "其中的", "里面", "之中", "各自",
                          "刚才", "上次", "之前", "前一个", "上一轮",
                          "名单", "名字", "姓名", "列表", "那些", "这些",
                          "那个", "这个", "那几", "这几",
                          "分别", "具体", "详细"}
        if any(w in question for w in _referral_words):
            if settings.feishu_bot_runtime_v5_enabled:
                return ctx
            try:
                import json as _rj
                import redis as _redis
                from app.core.config import settings as _settings
                _rc = _redis.Redis.from_url(_settings.redis_url, decode_responses=True)
                _raw = _rc.get(f'feishu:result:{chat_id}')
                if _raw:
                    result = _rj.loads(_raw)
                    if result.get("answer"):
                        ctx += f"\n上一条答案：{result['answer'][:800]}"
                    if result.get("name_list"):
                        ctx += f"\n人员名单：{result['name_list']}"
            except Exception:
                pass
        return ctx
    except Exception:
        pass
    return ""


def _runtime_conversation_context(chat_id: str) -> str:
    return conversation_context_text(chat_id)


def _runtime_context_pack_text(chat_id: str | None, question: str, *, purpose: str) -> str:
    if not chat_id:
        return ""
    return conversation_context_pack(chat_id, question=question, purpose=purpose).text


# ── Main rewrite function ───────────────────────────────────────
def rewrite_bot_answer(
    *,
    question: str,
    answer: str,
    actor: Any,
    scope_label: str,
    route_label: str,
    style_override: str | None = None,
    chat_id: str | None = None,
    is_casual: bool = False,
) -> str:
    if not should_rewrite_answer(answer=answer, route_label=route_label):
        return answer

    style = style_override or _style_for_actor(actor)
    open_id = getattr(actor, "open_id", "") or ""
    profile = _profile_text(open_id, style, actor=actor)

    # Detect casual vs business from route_path or answer content
    _is_casual = is_casual
    if not _is_casual:
        _question_lower = question[:100].lower()
        if any(w in _question_lower for w in ["你好", "在吗", "在线", "几点", "日期", "现在", "聊", "没事", "谢谢", "拜拜", "再见", "知道", "你叫", "你是谁"]):
            _is_casual = True
    if _is_casual:
        session_ctx = _runtime_context_pack_text(chat_id, question, purpose="conversation") or _get_session_context(chat_id, question)
        actor_name, actor_role = conversation_context_from_actor(actor)
        return conversation_llm_reply(
            ConversationLLMContext(
                question=question,
                fallback_answer=answer,
                actor_name=actor_name,
                actor_role=actor_role,
                profile_text=profile,
                session_context=session_ctx,
            )
        )
    session_ctx = _runtime_context_pack_text(chat_id, question, purpose="presentation") or _get_session_context(chat_id, question)
    return presentation_llm_rewrite(
        PresentationLLMContext(
            question=question,
            original_answer=answer,
            profile_text=profile,
            session_context=session_ctx,
            scope_label=scope_label,
        )
    )


def should_rewrite_answer(*, answer: str, route_label: str, route_path: str | None = None) -> bool:
    """Decide whether legacy non-V5 answers may use presentation rewrite."""
    if not settings.bot_llm_answer_rewrite_enabled or _running_tests():
        return False
    if not answer.strip():
        return False
    if len(answer) > settings.bot_llm_answer_rewrite_max_chars:
        return False
    if any(text in answer for text in ("未接入", "没有接入", "没有权限", "不能生成", "不会改用", "权限拦截", "需要授权")):
        return False
    if any(text in answer for text in ("请回复‘确认’", "已提交", "提交失败", "权限拦截")):
        return False
    # Skip LLM rewrite for approval/task list queries to preserve item count
    if any(skip in route_label for skip in ("飞书审批待办", "待办查询")):
        return False
    return True


# ── Public profile API ───────────────────────────────────────────────
def get_user_profile(open_id: str) -> dict:
    """Get or create a user profile. Returns a copy for external read."""
    return dict(_get_user_profile(open_id))


def update_user_profile(open_id: str, **kwargs) -> None:
    """Update profile fields. Persists to both PostgreSQL and Redis."""
    apply_profile_update(open_id, kwargs)


def profile_summary(open_id: str) -> str:
    """Human-readable profile summary for display/feedback."""
    p = _get_user_profile(open_id)
    style_map = {"formal": "正式", "casual": "轻松", "professional": "专业", "tech": "技术"}
    verb_map = {"concise": "简洁", "balanced": "适中", "detailed": "详细"}
    _s = style_map.get(p.get("style", ""), p.get("style", ""))
    _v = verb_map.get(p.get("verbosity", ""), p.get("verbosity", ""))
    _e = "是" if p.get("use_emoji") else "否"
    _n = p.get("interactions", 0)
    return f"沟通风格：{_s}\n详细程度：{_v}\n使用表情：{_e}\n交互次数：{_n}"


# ── Style helpers ───────────────────────────────────────────────
def _style_for_actor(actor: Any) -> str:
    role = getattr(actor, "role", "")
    domains = set(getattr(actor, "domains", ()) or ())
    if role == "owner":
        return "老板风格：简洁、直接、先结论、少解释"
    if "finance" in domains or "approval" in domains:
        return "财务风格：金额、对象、凭证、风险优先"
    if "sales" in domains:
        return "销售风格：客户、项目、交付和下一步优先"
    if "rd" in domains or "研发" in domains:
        return "研发风格：项目、问题、负责人、里程碑优先"
    return "员工风格：简明扼要，只说授权范围内的内容"


def _profile_fact_hint(actor: Any | None) -> str:
    if actor is None:
        return ""
    parts: list[str] = []
    display_name = str(getattr(actor, "display_name", "") or "").strip()
    role = str(getattr(actor, "role", "") or "").strip()
    job_title = str(getattr(actor, "job_title", "") or "").strip()
    email = str(getattr(actor, "email", "") or "").strip()
    department_names = tuple(str(item) for item in getattr(actor, "department_names", ()) or () if str(item).strip())
    if display_name:
        parts.append(f"姓名/称呼={display_name}")
    if role:
        parts.append(f"系统角色={role}")
    if job_title:
        parts.append(f"职位={job_title}")
    if department_names:
        parts.append(f"部门={','.join(department_names[:2])}")
    if email:
        parts.append(f"邮箱={email}")
    return "；".join(parts)


def _valid_rewrite(*, original: str, rewritten: str) -> bool:
    return valid_presentation_rewrite(original=original, rewritten=rewritten)


def _running_tests() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))
