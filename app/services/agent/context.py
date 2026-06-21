from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import BotUserPreference, BotUserSession, MemoryFact
from app.services.access_control import principal_from_bot_actor
from app.services.agent.policies import BotActor
from app.services.data.memory import memory_fact_access_condition
from app.services.tools.base import (
    DATA_BOUNDARY_POLICY,
    DATA_PERMISSION_MODEL,
    DIGITAL_ADVISOR_PERMISSION_POLICY,
    ENTERPRISE_IDENTITY_CONSTRAINTS,
    ENTERPRISE_IDENTITY_RESOURCE_OWNER,
    TOOL_ACCESS_POLICY,
    TOOL_SHARING_MODEL,
    USER_IDENTITY_CONSTRAINTS,
    USER_IDENTITY_RESOURCES,
    SHARED_TOOL_COUNT,
    identity_permission_contract,
)


SESSION_TTL_DAYS = 14


@dataclass(frozen=True)
class BotUserContext:
    answer_style: str | None
    favorite_modules: list[str]
    memory_facts: list[MemoryFact]


def bot_session_key(*, user_open_id: str, chat_id: str | None) -> str:
    return f"{user_open_id}:{chat_id or 'direct'}"


def bot_answer_style(db: Session | None, *, company_id: UUID, actor: BotActor) -> str:
    preference = get_bot_user_preference(db, company_id=company_id, user_open_id=actor.open_id)
    if preference and preference.answer_style:
        return _style_with_auto_context(
            preference.answer_style.strip(),
            db=db,
            company_id=company_id,
            user_open_id=actor.open_id,
        )
    memory_style = memory_answer_style(db, company_id=company_id, user_open_id=actor.open_id)
    if memory_style:
        return _style_with_auto_context(memory_style, db=db, company_id=company_id, user_open_id=actor.open_id)
    return _style_with_auto_context(
        fallback_answer_style(actor),
        db=db,
        company_id=company_id,
        user_open_id=actor.open_id,
    )


def memory_answer_style(db: Session | None, *, company_id: UUID, user_open_id: str | None) -> str | None:
    if db is None or not user_open_id:
        return None
    fact = db.scalar(
        select(MemoryFact)
        .where(MemoryFact.company_id == company_id)
        .where(MemoryFact.user_open_id == user_open_id)
        .where(MemoryFact.scope.in_(("personal", "user")))
        .where(MemoryFact.fact_type.in_(("answer_style", "communication_preference")))
        .order_by(MemoryFact.updated_at.desc())
    )
    return fact.content.strip() if fact and fact.content else None


def upsert_bot_answer_style(
    db: Session,
    *,
    company_id: UUID,
    user_open_id: str,
    answer_style: str,
    favorite_modules: list[str] | None = None,
) -> BotUserPreference:
    style = normalize_answer_style(answer_style)
    preference = get_bot_user_preference(db, company_id=company_id, user_open_id=user_open_id)
    if preference is None:
        preference = BotUserPreference(
            company_id=company_id,
            user_open_id=user_open_id,
            answer_style=style,
            favorite_modules=favorite_modules or [],
            settings={},
        )
        db.add(preference)
    else:
        preference.answer_style = style
        if favorite_modules is not None:
            preference.favorite_modules = favorite_modules

    subject = f"user:{user_open_id}:answer_style"
    fact = db.scalar(
        select(MemoryFact)
        .where(MemoryFact.company_id == company_id)
        .where(MemoryFact.subject == subject)
        .where(MemoryFact.fact_type == "answer_style")
    )
    if fact is None:
        fact = MemoryFact(
            company_id=company_id,
            fact_type="answer_style",
            subject=subject,
            content=style,
            confidence="high",
            scope="personal",
            user_open_id=user_open_id,
            source_kind="inferred_preference",
            payload={"managed_by": "bot_user_preferences"},
        )
        db.add(fact)
    else:
        fact.content = style
        fact.confidence = "high"
        fact.scope = "personal"
        fact.user_open_id = user_open_id
        fact.source_kind = "inferred_preference"
        fact.payload = {**(fact.payload or {}), "managed_by": "bot_user_preferences"}
    return preference


def normalize_answer_style(text: str) -> str:
    style = " ".join(str(text or "").replace("\n", " ").split())
    return style[:500] if style else "简洁、直接、先结论，再给必要依据和下一步。"


def recent_session_style_hint(db: Session | None, *, company_id: UUID, user_open_id: str | None) -> str | None:
    if db is None or not user_open_id:
        return None
    session = db.scalar(
        select(BotUserSession)
        .where(BotUserSession.company_id == company_id)
        .where(BotUserSession.user_open_id == user_open_id)
        .order_by(BotUserSession.updated_at.desc())
    )
    if not session or not session.short_context:
        return None
    context = session.short_context or {}
    last_question = _short(context.get("last_question"), 120)
    last_intent = _short(session.last_intent, 80)
    if not last_question and not last_intent:
        return None
    parts = []
    if last_intent:
        parts.append(f"最近意图：{last_intent}")
    if last_question:
        parts.append(f"上一轮问题：{last_question}")
    return "；".join(parts)


def _style_with_auto_context(
    base_style: str,
    *,
    db: Session | None,
    company_id: UUID,
    user_open_id: str | None,
) -> str:
    hint = recent_session_style_hint(db, company_id=company_id, user_open_id=user_open_id)
    auto_rule = "请结合用户当前问题、最近上下文和长期记忆自动判断表达方式，避免机械套模板。"
    if hint:
        return f"{base_style} {auto_rule} {hint}"
    return f"{base_style} {auto_rule}"


def fallback_answer_style(actor: BotActor) -> str:
    domains = set(actor.domains or ())
    if actor.role == "owner":
        return "老板风格：简洁、直接、先结论、少废话、给可执行建议。"
    if "finance" in domains or "approval" in domains:
        return "财务/审批负责人风格：金额、对象、凭证、风险优先，表达准确。"
    if "sales" in domains:
        return "销售负责人风格：客户、项目、交付和下一步优先。"
    if "rd" in domains or "研发" in domains:
        return "研发负责人风格：项目、问题、负责人、里程碑优先。"
    return "员工风格：只回答授权范围，清楚说明下一步，不暴露无关信息。"


def get_bot_user_preference(
    db: Session | None,
    *,
    company_id: UUID,
    user_open_id: str | None,
) -> BotUserPreference | None:
    if db is None or not user_open_id:
        return None
    return db.scalar(
        select(BotUserPreference)
        .where(BotUserPreference.company_id == company_id)
        .where(BotUserPreference.user_open_id == user_open_id)
    )


def load_bot_user_context(
    db: Session | None,
    *,
    company_id: UUID,
    actor: BotActor,
    chat_id: str | None,
    scope: str,
    limit: int = 6,
) -> BotUserContext:
    preference = get_bot_user_preference(db, company_id=company_id, user_open_id=actor.open_id)
    return BotUserContext(
        answer_style=preference.answer_style if preference else None,
        favorite_modules=list(preference.favorite_modules or []) if preference else [],
        memory_facts=search_memory_facts_for_actor(
            db,
            company_id=company_id,
            actor=actor,
            chat_id=chat_id,
            scope=scope,
            limit=limit,
        ),
    )


def search_memory_facts_for_actor(
    db: Session | None,
    *,
    company_id: UUID,
    actor: BotActor,
    chat_id: str | None,
    scope: str,
    limit: int = 6,
) -> list[MemoryFact]:
    if db is None:
        return []
    now = datetime.now(UTC)
    query = (
        select(MemoryFact)
        .where(MemoryFact.company_id == company_id)
        .where(or_(MemoryFact.expires_at.is_(None), MemoryFact.expires_at > now))
    )
    principal = principal_from_bot_actor(company_id=company_id, actor=actor, current_chat_id=chat_id)
    query = query.where(
        memory_fact_access_condition(
            MemoryFact,
            principal=principal,
            requested_scope=scope,
            chat_id=chat_id,
        )
    )
    return list(db.scalars(query.order_by(MemoryFact.updated_at.desc()).limit(limit)).all())


def record_bot_session(
    db: Session | None,
    *,
    company_id: UUID,
    actor: BotActor,
    chat_id: str | None,
    question: str,
    normalized_command: str,
    answer: str,
    route_label: str | None = None,
    scope_label: str | None = None,
) -> BotUserSession | None:
    if db is None or not actor.open_id:
        return None
    key = bot_session_key(user_open_id=actor.open_id, chat_id=chat_id)
    session = db.scalar(
        select(BotUserSession)
        .where(BotUserSession.company_id == company_id)
        .where(BotUserSession.session_key == key)
    )
    if session is None:
        session = BotUserSession(
            company_id=company_id,
            user_open_id=actor.open_id,
            chat_id=chat_id,
            session_key=key,
            short_context={},
        )
        db.add(session)
    session.last_intent = _short(normalized_command, 120)
    session.last_route = _short(route_label, 120)
    session.last_scope = _short(scope_label, 120)
    session.message_count = (session.message_count or 0) + 1
    session.expires_at = datetime.now(UTC) + timedelta(days=SESSION_TTL_DAYS)
    session.short_context = {
        "agent_identity": bot_session_agent_identity(company_id=company_id, actor=actor),
        "last_question": _short(question, 500),
        "last_answer": _short(answer, 800),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    return session


def bot_session_agent_identity(*, company_id: UUID, actor: BotActor) -> dict[str, Any]:
    owner_key = actor.open_id or actor.email or actor.display_name or actor.role or "unknown"
    data_access_scope = "company" if actor.can_query_company else actor.access_scope
    user_identity_required = data_access_scope in {"personal", "self", "user"}
    role_scope = {
        "role": actor.role,
        "access_scope": actor.access_scope,
        "domains": list(actor.domains),
        "company_data_allowed": actor.can_query_company,
    }
    return {
        "agent_type": "employee_personal_agent",
        "agent_id": f"{company_id}:{owner_key}",
        "company_id": str(company_id),
        "agent_owner_open_id": actor.open_id,
        "agent_owner_email": actor.email,
        "agent_owner_display_name": actor.display_name,
        "tool_access_policy": TOOL_ACCESS_POLICY,
        "tool_sharing_model": TOOL_SHARING_MODEL,
        "shared_business_tools": ["ApprovalTool","KnowledgeTool","BitableTool","ChatTool","CalendarTool","MeetingTool","ReportTool","AutomationTool","PeopleTool"],
        "shared_business_tool_count": SHARED_TOOL_COUNT,
        "agent_can_call_all_business_tools": True,
        "data_permission_model": DATA_PERMISSION_MODEL,
        "data_access_scope": data_access_scope,
        "identity_permission_contract": identity_permission_contract(user_identity_required=user_identity_required),
        "enterprise_identity": "app_identity",
        "enterprise_identity_constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
        "enterprise_resource_boundary": {
            "identity": "app_identity",
            "resource_owner": ENTERPRISE_IDENTITY_RESOURCE_OWNER,
            "constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
            "company_scope": str(company_id),
            "role_scope": role_scope,
            "can_exceed_feishu_app_permissions": False,
        },
        "user_identity": "resource_owner_identity" if user_identity_required else None,
        "user_identity_required": user_identity_required,
        "user_identity_constraints": list(USER_IDENTITY_CONSTRAINTS) if user_identity_required else [],
        "user_identity_supported_resources": list(USER_IDENTITY_RESOURCES),
        "user_resource_boundary": {
            "identity": "resource_owner_identity",
            "supported_resources": list(USER_IDENTITY_RESOURCES),
            "constraints": list(USER_IDENTITY_CONSTRAINTS),
            "required": user_identity_required,
            "owner_open_id": actor.open_id if user_identity_required else None,
            "can_exceed_original_authorization": False,
        },
        "data_boundary_policy": DATA_BOUNDARY_POLICY,
        "digital_advisor_permission_policy": DIGITAL_ADVISOR_PERMISSION_POLICY,
        "cannot_escalate_original_permissions": True,
    }


def _short(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] if text else None
