import os
import time as _time
from typing import Any

from app.core.config import settings
from app.db.session import SessionLocal as _SessionLocal
from app.services.conversation_context import conversation_context_text
from app.services.llm.conversation import ConversationLLMContext, conversation_context_from_actor, conversation_llm_reply
from app.services.llm.presentation import PresentationLLMContext, presentation_llm_rewrite, valid_presentation_rewrite

# ── User personality profiles ───────────────────────────────────
import json as _json
import redis as _redis
from sqlalchemy import text as _sql

# Ensure profiles table exists on first import
try:
    _db = _SessionLocal()
    _db.execute(_sql("CREATE TABLE IF NOT EXISTS user_profiles (open_id VARCHAR PRIMARY KEY, profile_json TEXT DEFAULT '{}', created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW())"))
    _db.commit()
    _db.close()
except Exception:
    pass
_redis_profiles = _redis.Redis.from_url(settings.redis_url, decode_responses=True)
_PROFILE_DEFAULTS = {
    "style": "professional",
    "verbosity": "balanced",
    "use_emoji": False,
    "use_formatting": True,
    "tone_tips": "",
    "interactions": 0,
    "last_updated": 0.0,
}
_PROFILE_TTL = 86400  # 24 hours


def _profile_key(open_id: str) -> str:
    return f"feishu:profile:{open_id}"


def _get_user_profile(open_id: str) -> dict:
    # 1. Try Redis cache
    try:
        raw = _redis_profiles.get(_profile_key(open_id))
        if raw:
            return _json.loads(raw)
    except Exception:
        pass
    # 2. Try PostgreSQL
    try:
        db = _SessionLocal()
        try:
            row = db.execute(_sql("SELECT profile_json FROM user_profiles WHERE open_id = :o"), {"o": open_id}).fetchone()
            if row and row[0]:
                data = _json.loads(row[0])
                # Cache in Redis
                try:
                    _redis_profiles.setex(_profile_key(open_id), _PROFILE_TTL, _json.dumps(data, ensure_ascii=False))
                except Exception:
                    pass
                return data
        finally:
            db.close()
    except Exception:
        pass
    return dict(_PROFILE_DEFAULTS)


def _profile_text(open_id: str, actor_style: str) -> str:
    """Return a short profile description for the LLM prompt."""
    p = _get_user_profile(open_id)
    verbosity = p.get("verbosity", "balanced")
    use_emoji = p.get("use_emoji", False)
    style = p.get("style", "professional")
    parts = [f"说话风格：{style}", f"详细程度：{verbosity}"]
    if use_emoji:
        parts.append("可以适当使用emoji")
    if p.get("tone_tips"):
        parts.append(f"额外提示：{p['tone_tips']}")
    if actor_style:
        parts.append(f"角色提示：{actor_style}")
    return "；".join(parts)


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
    profile = _profile_text(open_id, style)

    # Detect casual vs business from route_path or answer content
    _is_casual = is_casual
    if not _is_casual:
        _question_lower = question[:100].lower()
        if any(w in _question_lower for w in ["你好", "在吗", "在线", "几点", "日期", "现在", "聊", "没事", "谢谢", "拜拜", "再见", "知道", "你叫", "你是谁"]):
            _is_casual = True
    session_ctx = _get_session_context(chat_id, question)

    if _is_casual:
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
    """Decide whether to rewrite. Always try for all routes — LLM + validation filter bad results."""
    if not settings.bot_llm_answer_rewrite_enabled or _running_tests():
        return False
    if not answer.strip():
        return False
    if len(answer) > settings.bot_llm_answer_rewrite_max_chars:
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
    profile = _get_user_profile(open_id)
    allowed = {"style", "verbosity", "use_emoji", "tone_tips"}
    for k, v in kwargs.items():
        if k in allowed:
            profile[k] = v
    profile["interactions"] = profile.get("interactions", 0) + 1
    profile["last_updated"] = _time.time()
    # Save to Redis
    try:
        _redis_profiles.setex(_profile_key(open_id), _PROFILE_TTL, _json.dumps(profile, ensure_ascii=False))
    except Exception:
        pass
    # Save to PostgreSQL
    try:
        db = _SessionLocal()
        try:
            _json_str = _json.dumps(profile, ensure_ascii=False)
            existing = db.execute(_sql("SELECT 1 FROM user_profiles WHERE open_id = :o"), {"o": open_id}).fetchone()
            if existing:
                db.execute(_sql("UPDATE user_profiles SET profile_json = :j, updated_at = NOW() WHERE open_id = :o"),
                          {"j": _json_str, "o": open_id})
            else:
                db.execute(_sql("INSERT INTO user_profiles (open_id, profile_json, created_at, updated_at) VALUES (:o, :j, NOW(), NOW())"),
                          {"o": open_id, "j": _json_str})
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


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


def _valid_rewrite(*, original: str, rewritten: str) -> bool:
    return valid_presentation_rewrite(original=original, rewritten=rewritten)


def _running_tests() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))
