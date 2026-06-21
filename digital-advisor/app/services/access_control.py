from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Session

from app.models.entities import Resource, ResourcePermission, Role, User, UserCompanyRole
from app.services.agent.policies import BotActor
from app.services.v5_resources import normalize_v5_resource_type


MANAGEMENT_ROLES = {"owner", "admin", "ceo", "management"}
DOMAIN_ROLE_MAP = {
    "finance": {"finance_manager", "owner", "admin", "ceo"},
    "sales": {"sales_manager", "owner", "admin", "ceo"},
    "customer": {"sales_manager", "owner", "admin", "ceo"},
    "rd": {"rd_manager", "owner", "admin", "ceo"},
    "project": {"project_manager", "rd_manager", "owner", "admin", "ceo"},
    "approval": {"finance_manager", "management", "owner", "admin", "ceo"},
    "knowledge": {"rd_manager", "management", "owner", "admin", "ceo"},
    "doc": {"rd_manager", "management", "owner", "admin", "ceo"},
    "bitable": {"sales_manager", "finance_manager", "project_manager", "owner", "admin", "ceo"},
}
RESOURCE_DOMAIN_MAP = {
    "chat": "communications",
    "mailbox": "mail",
    "approval": "approval",
    "bitable": "bitable",
    "doc": "doc",
    "wiki": "knowledge",
    "calendar": "calendar",
    "meeting": "meeting",
    "task": "task",
    "directory": "administration",
}
SENSITIVE_BITABLE_FIELDS = {
    "salary",
    "薪资",
    "工资",
    "奖金",
    "成本",
    "利润",
    "毛利",
    "客户电话",
    "手机号",
    "身份证",
    "银行账号",
}


@dataclass(frozen=True)
class AccessPrincipal:
    company_id: UUID
    role: str = "employee"
    user_id: UUID | None = None
    open_id: str | None = None
    department_ids: tuple[UUID, ...] = field(default_factory=tuple)
    team_ids: tuple[UUID, ...] = field(default_factory=tuple)
    domains: tuple[str, ...] = field(default_factory=tuple)
    current_chat_id: str | None = None
    all_companies: bool = False

    @property
    def can_query_company(self) -> bool:
        return self.role in {"owner", "admin"} or (self.role == "ceo" and "all" in self.domains)

    @property
    def is_management(self) -> bool:
        return self.role in MANAGEMENT_ROLES or self.can_query_company


def build_principal_for_user(
    db: Session,
    *,
    company_id: UUID,
    user_id: UUID | None = None,
    open_id: str | None = None,
    current_chat_id: str | None = None,
) -> AccessPrincipal:
    if user_id is None and open_id:
        user = db.scalar(select(User).where(User.external_user_id == open_id))
        user_id = user.id if user else None
    if user_id is None:
        return AccessPrincipal(company_id=company_id, open_id=open_id, current_chat_id=current_chat_id)
    rows = db.execute(
        select(UserCompanyRole, Role)
        .join(Role, Role.id == UserCompanyRole.role_id)
        .where(UserCompanyRole.company_id == company_id)
        .where(UserCompanyRole.user_id == user_id)
        .where(UserCompanyRole.is_active.is_(True))
    ).all()
    roles = [role.name for _, role in rows]
    role = _highest_role(roles)
    domains = _unique(
        domain
        for _, role_obj in rows
        for domain in ((role_obj.permissions or {}).get("domains") or [])
    )
    department_ids = tuple(row.department_id for row, _ in rows if row.department_id)
    team_ids = tuple(row.team_id for row, _ in rows if row.team_id)
    return AccessPrincipal(
        company_id=company_id,
        role=role,
        user_id=user_id,
        open_id=open_id,
        department_ids=department_ids,
        team_ids=team_ids,
        domains=tuple(domains),
        current_chat_id=current_chat_id,
    )


def principal_from_bot_actor(*, company_id: UUID, actor: BotActor, current_chat_id: str | None = None) -> AccessPrincipal:
    return AccessPrincipal(
        company_id=company_id,
        role=actor.role,
        open_id=actor.open_id,
        domains=tuple(actor.domains),
        current_chat_id=current_chat_id,
        all_companies=actor.access_scope == "all",
    )


def resource_access_policy(resource: Resource, *, principal: AccessPrincipal | None = None) -> dict[str, Any]:
    resource_type = normalize_v5_resource_type(resource.resource_type, resource.resource_id)
    data_classification = getattr(resource, "data_classification", "company")
    business_domain = _resource_business_domain(resource, resource_type=resource_type)
    visibility_scope = getattr(resource, "permission_level", None) or "company"
    policy = {
        "resource_id": str(resource.id),
        "resource_type": resource_type,
        "data_classification": data_classification,
        "business_domain": business_domain,
        "visibility_scope": visibility_scope,
        "access_boundary": _access_boundary(resource_type, data_classification, visibility_scope),
        "requires_pre_llm_filter": True,
        "field_policy": _field_policy(resource_type),
        "record_policy": _record_policy(resource_type),
    }
    if principal is not None:
        policy["principal_can_access"] = can_access_resource(resource, principal)
        policy["principal_reason"] = _resource_access_reason(resource, principal)
    return policy


def can_access_resource(resource: Resource, principal: AccessPrincipal) -> bool:
    if resource.company_id != principal.company_id and not principal.all_companies:
        return False
    if principal.can_query_company:
        return True
    classification = getattr(resource, "data_classification", "company")
    visibility_scope = getattr(resource, "permission_level", "company")
    resource_type = normalize_v5_resource_type(resource.resource_type, resource.resource_id)
    business_domain = _resource_business_domain(resource, resource_type=resource_type)

    if classification == "personal":
        return _is_personal_owner(resource, principal)
    if visibility_scope in {"owner", "private"}:
        return False
    if visibility_scope in {"company_management", "management"}:
        return principal.is_management
    if resource_type == "chat":
        return _chat_access(resource, principal)
    if business_domain in principal.domains or resource_type in principal.domains:
        return True
    return principal.role in DOMAIN_ROLE_MAP.get(business_domain, set())


def _resource_business_domain(resource: Resource, *, resource_type: str) -> str:
    return getattr(resource, "business_domain", None) or RESOURCE_DOMAIN_MAP.get(resource_type, "general")


def work_event_access_condition(model: Any, principal: AccessPrincipal) -> Any:
    company_condition = true_company_condition(model, principal)
    if principal.can_query_company:
        return company_condition
    own_tokens = _principal_tokens(principal)
    personal_conditions = []
    if own_tokens:
        personal_conditions.append(model.allowed_user_ids.has_any(own_tokens))
        personal_conditions.append(model.source_account_id.in_(own_tokens))
    company_conditions = [
        model.data_classification == "company",
        model.visibility_scope.notin_(["owner", "private"]),
    ]
    allowed = [
        and_(*company_conditions, model.visibility_scope.in_(["public", "company"])),
        and_(*company_conditions, model.business_domain.in_(list(principal.domains or ()))),
        and_(*company_conditions, model.allowed_roles.has_any(_role_tokens(principal))),
    ]
    if principal.is_management:
        allowed.append(and_(*company_conditions, model.visibility_scope.in_(["company_management", "management"])))
    if principal.current_chat_id:
        allowed.append(and_(*company_conditions, model.thread_id == principal.current_chat_id))
    if principal.department_ids:
        allowed.append(and_(*company_conditions, model.allowed_departments.has_any([str(item) for item in principal.department_ids])))
    if personal_conditions:
        allowed.append(and_(model.data_classification == "personal", or_(*personal_conditions)))
    if not allowed:
        return false()
    return and_(company_condition, or_(*allowed))


def resource_access_condition(model: Any, principal: AccessPrincipal) -> Any:
    company_condition = true_company_condition(model, principal)
    if principal.can_query_company:
        return company_condition
    allowed = [
        and_(model.data_classification == "company", model.permission_level.in_(["public", "company"])),
        and_(model.data_classification == "company", model.business_domain.in_(list(principal.domains or ()))),
    ]
    if principal.is_management:
        allowed.append(and_(model.data_classification == "company", model.permission_level.in_(["company_management", "management"])))
    if principal.current_chat_id:
        allowed.append(and_(model.data_classification == "company", model.resource_id == principal.current_chat_id))
    return and_(company_condition, or_(*allowed))


def true_company_condition(model: Any, principal: AccessPrincipal) -> Any:
    if principal.all_companies:
        return model.company_id.is_not(None)
    return model.company_id == principal.company_id


def access_preview_for_resource(db: Session, *, resource_id: UUID, principal: AccessPrincipal | None = None) -> dict[str, Any]:
    resource = db.get(Resource, resource_id)
    if resource is None:
        return {"available": False, "reason": "resource_not_found"}
    explicit_permissions = db.execute(
        select(ResourcePermission, Role, User)
        .outerjoin(Role, Role.id == ResourcePermission.role_id)
        .outerjoin(User, User.id == ResourcePermission.user_id)
        .where(ResourcePermission.resource_id == resource.id)
        .where(ResourcePermission.enabled.is_(True))
    ).all()
    policy = resource_access_policy(resource, principal=principal)
    policy["explicit_permissions"] = [
        {
            "permission_level": permission.permission_level,
            "role": role.name if role else None,
            "user": user.display_name if user else None,
            "conditions_json": permission.conditions_json,
        }
        for permission, role, user in explicit_permissions
    ]
    return {"available": True, "policy": policy}


def _access_boundary(resource_type: str, data_classification: str, visibility_scope: str) -> str:
    if data_classification == "personal":
        return "owner_only"
    if resource_type == "chat":
        return "chat_members_or_authorized_role"
    if resource_type == "mailbox":
        return "owner_only_mail_account"
    if resource_type == "bitable":
        return "table_view_record_field_acl"
    if resource_type in {"doc", "wiki"}:
        return "document_acl_or_authorized_knowledge_domain"
    return visibility_scope


def _field_policy(resource_type: str) -> dict[str, Any]:
    if resource_type == "bitable":
        return {
            "mode": "mask_sensitive_fields_before_llm",
            "sensitive_field_names": sorted(SENSITIVE_BITABLE_FIELDS),
            "default": "visible_after_resource_and_record_acl",
        }
    if resource_type in {"doc", "wiki"}:
        return {"mode": "chunk_acl", "default": "inherit_document_acl"}
    return {"mode": "resource_acl", "default": "inherit_resource_acl"}


def _record_policy(resource_type: str) -> dict[str, Any]:
    if resource_type == "bitable":
        return {
            "mode": "record_acl",
            "owner_fields": ["owner", "负责人", "销售", "项目经理"],
            "department_fields": ["department", "部门"],
            "default": "authorized_business_domain_only",
        }
    if resource_type in {"doc", "wiki"}:
        return {"mode": "document_acl", "default": "authorized_document_only"}
    return {"mode": "resource_acl", "default": "authorized_resource_only"}


def _resource_access_reason(resource: Resource, principal: AccessPrincipal) -> str:
    if principal.can_query_company:
        return "company_admin"
    if getattr(resource, "data_classification", "company") == "personal":
        return "personal_owner" if _is_personal_owner(resource, principal) else "personal_denied"
    if normalize_v5_resource_type(resource.resource_type, resource.resource_id) == "chat":
        return "current_chat_or_domain" if _chat_access(resource, principal) else "not_chat_member_or_authorized_domain"
    if getattr(resource, "business_domain", "general") in principal.domains:
        return "authorized_business_domain"
    return "default_resource_policy"


def _chat_access(resource: Resource, principal: AccessPrincipal) -> bool:
    if principal.current_chat_id and resource.resource_id == principal.current_chat_id:
        return True
    return bool(set(principal.domains) & {"communications", "chat", getattr(resource, "business_domain", "general")})


def _is_personal_owner(resource: Resource, principal: AccessPrincipal) -> bool:
    config = resource.config_json or {}
    tokens = set(_principal_tokens(principal))
    return bool(tokens & {str(config.get("account_id") or ""), str(config.get("open_id") or ""), str(config.get("user_id") or "")})


def _principal_tokens(principal: AccessPrincipal) -> list[str]:
    return [str(item) for item in (principal.user_id, principal.open_id) if item]


def _role_tokens(principal: AccessPrincipal) -> list[str]:
    return [principal.role, *principal.domains]


def _highest_role(roles: list[str]) -> str:
    priority = ["owner", "admin", "ceo", "management", "finance_manager", "sales_manager", "rd_manager", "project_manager"]
    for role in priority:
        if role in roles:
            return role
    return roles[0] if roles else "employee"


def _unique(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(str(value))
    return result
