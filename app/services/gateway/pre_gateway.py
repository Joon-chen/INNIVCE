"""Pre Gateway — Step 1 of the Agent Runtime pipeline.

Responsibilities:
  Identity Loading      — Load user identity (user_id, role, company, department)
  Session Loading       — Load conversation context from Redis
  Rule Matching         — Handle fixed commands (/help, /menu, /reset)
  Query Rewrite         — Complete incomplete queries using session context

Strictly no business logic, no data queries, no tool calls.
"""

import json
from dataclasses import dataclass

from app.core.config import settings
from app.services.feishu.identity import BotIdentity
from app.services.llm.answer_semantics import _load_session


@dataclass
class PreGatewayContext:
    """Output of Pre Gateway processing."""

    rewritten_question: str
    canonical_question: str = ""
    session: dict | None = None
    profile: dict | None = None
    matched_rule: str | None = None
    rule_reply: str | None = None

    # Identity fields
    user_id: str = ""
    role: str = ""
    company_id: str = ""
    department_id: str = ""

    # Session fields
    active_company: str = ""
    active_department: str = ""
    active_project: str = ""
    last_intent: str = ""
    # Result Follow-up fields
    is_result_followup: bool = False
    followup_type: str = ""


def _detect_style_feedback(question: str, profile: dict | None, identity: BotIdentity) -> str | None:
    """Detect style feedback using local LLM. Updates profile if feedback detected."""
    if not identity or not identity.open_id:
        return None
    try:
        from app.services.llm.gateway import LLMGateway
        prompt = (
            "判断用户这句话是否在抱怨机器人的沟通方式、语气或称呼。"
            "如果是，提取具体的反馈内容，一句话概括（如不要直呼姓名/说话太客套/语气不对/太啰嗦/不够尊重等）。"
            "如果不是，只输出 null。不要输出其他内容。\n\n"
            f"用户消息：{question[:200]}"
        )
        result = (LLMGateway().complete_text(prompt, temperature=0.0) or "").strip()
        if result and result.lower() != "null":
            from app.services.llm.answer_rewriter import update_user_profile
            update_user_profile(identity.open_id, tone_tips=result)
            return "好的，已了解。"
    except Exception:
        pass
    return None



# ── Result Follow-up Detector ──────────────────────────────────────
def _detect_result_followup(question: str, chat_id: str | None) -> tuple:
    """Detect if current question continues the previous result.
    Returns: (is_followup, followup_type, result_answer)
    Does NOT call any LLM or external API.
    """
    if not chat_id:
        return (False, "", None)
    if settings.feishu_bot_runtime_v5_enabled:
        return (False, "", None)
    # Pronoun reference
    _pronoun_words = {"他们", "她们", "这些人", "这些单子", "这些审批", "这些任务"}
    # Result expansion
    _expand_words = {"详情", "详细", "展开", "全部", "具体", "列出来", "显示", "展开看看", "看看", "全部显示"}
    # Position reference
    _position_words = {"第一个", "最后一个", "第二个", "第三个"}
    matched_type = None
    if any(w in question for w in _pronoun_words):
        matched_type = "pronoun"
    elif any(w in question for w in _expand_words):
        matched_type = "expand"
    elif any(w in question for w in _position_words):
        matched_type = "position"
    if not matched_type:
        return (False, "", None)
    # Load result context from Redis
    try:
        from app.core.config import settings as _st
        import json as _rj
        import redis as _rd

        _rc = _rd.Redis.from_url(_st.redis_url, decode_responses=True)
        _raw = _rc.get(f"feishu:result:{chat_id}")
        if _raw:
            result = _rj.loads(_raw)
            answer = result.get("answer", "")
            if answer:
                return (True, matched_type, answer)
    except Exception:
        pass
    return (False, "", None)
def process_pre_gateway(
    question: str,
    identity: BotIdentity,
    chat_id: str | None = None,
    company_id: str | None = None,
) -> PreGatewayContext:
    """Execute Pre Gateway pipeline.

    Steps:
      1. Identity Loading  — extract user identity fields
      2. Session Loading    — load Redis session for this chat
      3. Profile Loading    — load user communication profile from Redis
      4. Rule Matching      — check fixed commands
      5. Query Rewrite      — complete incomplete queries with session context
    """
    # ── 1. Identity Loading ────────────────────────────────────
    ctx = PreGatewayContext(
        rewritten_question=question,
        user_id=identity.open_id or "",
        role=identity.role or "",
        company_id=company_id or "",
        department_id=getattr(identity, "department_id", "") or "",
    )

    # ── 2. Session Loading ─────────────────────────────────────
    session_data = _load_session(chat_id) if chat_id else None
    if session_data:
        ctx.session = session_data
        ctx.active_company = session_data.get("active_company", "") or ""
        ctx.active_department = session_data.get("active_department", "") or ""
        ctx.active_project = session_data.get("active_project", "") or ""
        ctx.last_intent = session_data.get("route_hint", "") or ""

    # ── 3. Profile Loading ─────────────────────────────────────
    profile = _load_profile(identity.open_id) if identity.open_id else None
    ctx.profile = profile

    # ── 3b. Legacy Style Feedback Detection ─────────────────────
    # V5 Constitution: Pre Gateway may load Profile, but must not update Profile,
    # call LLM for answer generation, or directly produce an answer.
    if not settings.feishu_bot_runtime_v5_enabled:
        _feedback_msg = _detect_style_feedback(question, profile, identity)
        if _feedback_msg:
            ctx.rule_reply = _feedback_msg
            ctx.matched_rule = "style_feedback"
            return ctx

    # ── 4. Rule Matching ───────────────────────────────────────
    cmd = question.strip().lower()
    # Keep /reset, let LLM handle help/menu naturally
    if cmd == "/reset":
        from app.services.llm.answer_semantics import _clear_session as _cs
        if chat_id:
            _cs(chat_id)
        ctx.matched_rule = "/reset"
        ctx.rule_reply = "会话已清除。"
        return ctx

    # ── 4.5 Result Follow-up Detector ──────────────────────────
    _is_fu, _fu_type, _fu_answer = _detect_result_followup(question, chat_id)
    if _is_fu:
        ctx.is_result_followup = True
        ctx.followup_type = _fu_type
        ctx.rule_reply = _fu_answer
        return ctx

    # ── 5. Query Rewrite ───────────────────────────────────────
    rewritten = _rewrite_query(question, session_data)
    ctx.rewritten_question = rewritten

    return ctx


def _load_profile(open_id: str) -> dict | None:
    """Load user profile from PostgreSQL + Redis cache."""
    try:
        from app.services.llm.answer_rewriter import _get_user_profile as _gup
        return _gup(open_id)
    except Exception:
        return None


def _rewrite_query(question: str, session: dict | None) -> str:
    """Rewrite incomplete queries with session context.

    Examples:
      - "研发呢" + session{active_company="固势", active_dept="半导体"}
        → "固势半导体事业部研发情况"
      - "那女生呢" + last_intent="feishu_contact_organization_snapshot"
        → "公司有多少个女生"
    """
    if not session or len(question) > 15:
        return question

    active_company = (session.get("active_company") or "").strip()
    active_dept = (session.get("active_department") or "").strip()
    last_intent = (session.get("route_hint") or "").strip()

    # If question is a follow-up pronoun/referral, use LLM rewrite
    _follow_words = {"那", "这个", "这些", "他们", "她们", "其中", "这里", "那边"}
    if any(w in question for w in _follow_words) and last_intent:
        return _llm_rewrite(question, session)

    # If question is short and has context, prefix with company/dept
    if len(question) <= 6 and active_company:
        parts = [active_company]
        if active_dept and not any(kw in question for kw in ("部门", "部", "组")):
            parts.append(active_dept)
        prefix = "".join(parts)
        return f"{prefix}{question}" if not question.startswith(prefix) else question

    return question


def _llm_rewrite(question: str, session: dict) -> str:
    """Use DeepSeek to intelligently rewrite the question with session context."""
    try:
        from app.services.llm.gateway import LLMGateway

        context = json.dumps(
            {
                "active_company": session.get("active_company", ""),
                "active_department": session.get("active_department", ""),
                "active_project": session.get("active_project", ""),
                "last_question": session.get("question", ""),
                "last_answer": session.get("answer", ""),
            },
            ensure_ascii=False,
        )
        prompt = (
            f"用户新问题：{question}\n"
            f"对话上下文：{context}\n"
            f"根据上下文补全用户问题，输出完整的提问。直接输出文本，不要加额外内容。"
        )
        result = (LLMGateway().complete_deepseek_text(prompt, temperature=0.0) or "").strip()
        return result or question
    except Exception:
        return question
