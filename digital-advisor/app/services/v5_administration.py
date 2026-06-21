from contextlib import nullcontext
from dataclasses import dataclass
from collections.abc import Mapping
import json
import re
import shutil
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy import func
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.serialization import json_safe
from app.models.entities import (
    AuditLog,
    BotUserAccess,
    Company,
    CompanySetting,
    Department,
    FeishuAppConfig,
    Permission,
    Resource,
    ResourcePermission,
    Role,
    Team,
    User,
    UserCompanyRole,
    WorkEvent,
)
from app.services.feishu.cli_profile import feishu_app_cli_profile
from app.services.feishu_admin_apps import FEISHU_APP_CREDENTIAL_STATUS_VALID
from app.services.tools.base import identity_permission_contract
from app.services.tools.providers.devops import run_feishu_cli_doctor_offline
from app.services.user_identity_authorizations import default_user_identity_authorizations
from app.services.access_control import AccessPrincipal, access_preview_for_resource, build_principal_for_user
from app.services.v5_resources import resource_to_v5_payload


DEFAULT_V5_ROLES: tuple[dict[str, Any], ...] = (
    {
        "name": "owner",
        "description": "企业老板/最高管理员，可访问授权公司范围内全部内部数据。",
        "scope_type": "all_companies",
        "permissions": {"domains": ["all"], "can_query_company": True, "can_manage": True},
    },
    {
        "name": "ceo",
        "description": "总经理，可访问经营管理、项目、销售、财务等管理数据。",
        "scope_type": "single_company",
        "permissions": {"domains": ["management", "finance", "sales", "project"], "can_query_company": True},
    },
    {
        "name": "management",
        "description": "管理层，可访问授权业务域和部门数据。",
        "scope_type": "department",
        "permissions": {"domains": ["management"], "can_query_company": False},
    },
    {
        "name": "finance_manager",
        "description": "财务负责人，可访问财务、付款、报销、薪酬相关数据。",
        "scope_type": "department",
        "permissions": {"domains": ["finance", "approval", "mail"]},
    },
    {
        "name": "sales_manager",
        "description": "销售负责人，可访问客户、销售群、销售项目和销售多维表格。",
        "scope_type": "department",
        "permissions": {"domains": ["sales", "customer", "mail", "bitable"]},
    },
    {
        "name": "rd_manager",
        "description": "研发负责人，可访问研发项目、技术文档和研发知识库。",
        "scope_type": "department",
        "permissions": {"domains": ["rd", "project", "knowledge", "doc"]},
    },
    {
        "name": "project_manager",
        "description": "项目负责人，可访问授权项目、项目任务和项目群数据。",
        "scope_type": "project",
        "permissions": {"domains": ["project", "task", "meeting"]},
    },
    {
        "name": "engineer",
        "description": "工程师，可访问个人相关任务、项目协作和授权知识。",
        "scope_type": "team",
        "permissions": {"domains": ["project", "task"], "can_query_company": False},
    },
    {
        "name": "employee",
        "description": "普通员工，默认仅访问与本人、所在会话或显式授权相关数据。",
        "scope_type": "team",
        "permissions": {"domains": ["chat", "task"], "can_query_company": False},
    },
    {
        "name": "guest",
        "description": "外部或临时用户，仅访问公开或显式授权内容。",
        "scope_type": "team",
        "permissions": {"domains": [], "can_query_company": False},
    },
)


DEFAULT_V5_PERMISSIONS: tuple[dict[str, Any], ...] = (
    {
        "code": "workspace.read",
        "name": "查看工作台",
        "description": "查看日报、任务、会议、审批和通知摘要。",
        "permission_type": "workspace",
        "config_json": {"module": "workspace", "level": "read"},
    },
    {
        "code": "resources.manage",
        "name": "管理资源中心",
        "description": "发现、登记、同步和监控飞书资源。",
        "permission_type": "resources",
        "config_json": {"module": "resources", "level": "manage"},
    },
    {
        "code": "knowledge.read",
        "name": "查看知识库",
        "description": "查看授权范围内的内部知识、关系和记忆。",
        "permission_type": "knowledge",
        "config_json": {"module": "knowledge", "level": "read", "boundary": "internal_only"},
    },
    {
        "code": "administration.manage",
        "name": "管理权限配置",
        "description": "管理公司、部门、团队、用户、角色、权限、机器人权限和审计日志。",
        "permission_type": "administration",
        "config_json": {"module": "administration", "level": "manage"},
    },
    {
        "code": "reports.read",
        "name": "查看报告中心",
        "description": "查看日报、周报、月报、风险和决策报告。",
        "permission_type": "reports",
        "config_json": {"module": "reports", "level": "read"},
    },
    {
        "code": "feishu_resources.manage",
        "name": "管理飞书资源策略",
        "description": "管理各公司飞书应用、机器人、资源发现、登记、同步策略和监控。",
        "permission_type": "resources",
        "config_json": {"module": "resources", "section": "feishu", "level": "manage"},
    },
)


@dataclass(frozen=True)
class V5AdministrationBootstrapResult:
    companies: int = 0
    bot_users: int = 0
    company_settings: int = 0
    roles: int = 0
    permissions: int = 0
    users: int = 0
    user_roles: int = 0
    resources: int = 0
    resource_permissions: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "companies": self.companies,
            "bot_users": self.bot_users,
            "company_settings": self.company_settings,
            "roles": self.roles,
            "permissions": self.permissions,
            "users": self.users,
            "user_roles": self.user_roles,
            "resources": self.resources,
            "resource_permissions": self.resource_permissions,
        }


def bootstrap_v5_administration(db: Session, company_id: UUID | None = None) -> V5AdministrationBootstrapResult:
    companies = _target_companies(db, company_id)
    counters = {
        "companies": 0,
        "bot_users": 0,
        "company_settings": 0,
        "roles": 0,
        "permissions": 0,
        "users": 0,
        "user_roles": 0,
        "resources": 0,
        "resource_permissions": 0,
    }
    if not companies and company_id is None:
        company, company_created = _ensure_default_launch_company(db)
        companies = [company]
        counters["companies"] += int(company_created)
    for company in companies:
        counters["bot_users"] += _ensure_default_owner_agent(db, company)
        counters["company_settings"] += _ensure_company_setting(db, company)
        role_map, created_roles = _ensure_default_roles(db, company)
        counters["roles"] += created_roles
        counters["permissions"] += _ensure_default_permissions(db, company)
        created_users, created_user_roles = _migrate_bot_users(db, company=company, role_map=role_map)
        counters["users"] += created_users
        counters["user_roles"] += created_user_roles
        counters["resources"] += _migrate_feishu_resources(db, company)
        counters["resources"] += _register_feishu_apps_as_bot_resources(db, company)
        db.flush()
        counters["resource_permissions"] += _ensure_owner_resource_permissions(db, company, role_map)
    db.commit()
    return V5AdministrationBootstrapResult(**counters)


def legacy_bot_role_to_v5_role(role: str | None) -> str:
    normalized = (role or "").lower().strip()
    if normalized in {"owner", "admin", "boss"}:
        return "owner"
    if normalized in {"ceo", "general_manager"}:
        return "ceo"
    if normalized in {"manager", "management"}:
        return "management"
    if normalized in {"guest", "external"}:
        return "guest"
    return "employee"


def legacy_scope_to_v5_scope(scope: str | None) -> str:
    normalized = (scope or "").lower().strip()
    if normalized in {"all", "all_companies"}:
        return "all_companies"
    if normalized in {"company", "single_company"}:
        return "single_company"
    if normalized == "domain":
        return "department"
    if normalized == "project":
        return "project"
    return "team"


def v5_os_overview(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    db_error = None
    try:
        company_query = select(Company).order_by(Company.created_at.asc())
        if company_id:
            company_query = company_query.where(Company.id == company_id)
        companies = list(db.scalars(company_query).all())
        company_payloads = [
            {
                "id": str(company.id),
                "name": company.name,
                "code": company.code,
                "status": getattr(company, "status", "active"),
                "settings": company_setting_payload(db, company.id),
                "counts": company_counts(db, company.id),
            }
            for company in companies
        ]
        total_counts = {
            "companies": len(companies),
            "settings": count_company_items(db, CompanySetting, company_id=company_id),
            "resources": count_company_items(db, Resource, company_id=company_id),
            "users": count_v5_users(db, company_id=company_id),
            "roles": count_company_items(db, Role, company_id=company_id),
            "permissions": count_company_items(db, Permission, company_id=company_id),
            "work_events": count_company_items(db, WorkEvent, company_id=company_id),
        }
    except (OperationalError, ProgrammingError) as exc:
        db_error = str(exc)[:240]
        company_payloads = []
        total_counts = {
            "companies": 0,
            "settings": 0,
            "resources": 0,
            "users": 0,
            "roles": 0,
            "permissions": 0,
            "work_events": 0,
        }
    database_status = "unavailable" if db_error else "ok"
    active_feishu_app_count = _active_feishu_app_count(db) if not db_error else 0
    validated_feishu_app_count = _validated_feishu_app_count(db) if not db_error else 0
    cli_profiles = _feishu_cli_profiles(db) if not db_error else []
    cli_readiness = _feishu_cli_readiness_check(cli_profiles)
    feishu_app_bootstrap_hint = _feishu_app_bootstrap_hint(
        cli_readiness,
        active_feishu_app_count=active_feishu_app_count,
        validated_feishu_app_count=validated_feishu_app_count,
    )
    release_blockers = ["database_unavailable"] if db_error else []
    if not db_error:
        if active_feishu_app_count == 0:
            release_blockers.append("feishu_app_config_missing")
        elif validated_feishu_app_count == 0:
            release_blockers.append("feishu_app_credentials_unvalidated")
    if not settings.feishu_bot_ai_mode_enabled:
        release_blockers.append("feishu_bot_ai_disabled")
    if cli_readiness["status"] == "blocked":
        release_blockers.append(cli_readiness["blocker"])
    release_readiness = {
        "status": "degraded" if release_blockers else "ready",
        "blockers": release_blockers,
        "checks": [
            {
                "key": "feishu_bot",
                "label": "大飞哥入口",
                "status": "ready" if settings.feishu_bot_ai_mode_enabled else "attention",
                "detail": "AI 兜底已开启" if settings.feishu_bot_ai_mode_enabled else "AI 兜底未开启",
            },
            {
                "key": "feishu_app_config",
                "label": "飞书应用配置",
                "status": "ready" if validated_feishu_app_count > 0 else "blocked",
                "detail": _feishu_app_config_readiness_detail(
                    active_feishu_app_count=active_feishu_app_count,
                    validated_feishu_app_count=validated_feishu_app_count,
                ),
                "required_action": (
                    feishu_app_bootstrap_hint["required_action"]
                    if active_feishu_app_count == 0
                    else "调用 tenant-access-token 校验 App Secret；校验通过后继续按公司独立 AppConfig 和 CLI Profile 接入后续公司"
                    if validated_feishu_app_count == 0
                    else "继续按公司独立 AppConfig 和 CLI Profile 接入后续公司"
                ),
                "bootstrap_hint": feishu_app_bootstrap_hint,
                "identity_boundary": "App Identity + Company Scope + Role Scope",
                "sync_boundary": "Sync Engine -> API Client -> Feishu -> PostgreSQL",
            },
            {
                "key": "admin_console",
                "label": "管理后台",
                "status": "ready",
                "detail": "V5 经营后台已启用",
            },
            {
                "key": "ios_app",
                "label": "iOS APP",
                "status": "planned",
                "detail": "后续开发",
            },
            {
                "key": "database",
                "label": "数据库",
                "status": "blocked" if db_error else "ready",
                "detail": "数据库未连接，首屏降级可读" if db_error else "数据库连接正常",
            },
            cli_readiness,
            {
                "key": "feishu_boundary",
                "label": "飞书职责边界",
                "status": "ready",
                "detail": "实时操作走 Tool Router/MCP/CLI，同步入库走 API Client",
            },
        ],
    }
    return {
        "version": "v5",
        "positioning": "Digital Chief of Staff, not a chatbot",
        "status": {"database": database_status, "error": db_error} if db_error else {"database": database_status},
        "principles": [
            "Feishu is the operational platform",
            "Digital Advisor owns analysis, memory, reasoning, and reporting",
            "Every query must be permission-filtered before retrieval",
            "Internal knowledge and external intelligence stay separated",
        ],
        "entrypoints": {
            "feishu_bot": {
                "name": "大飞哥",
                "ai_mode_enabled": settings.feishu_bot_ai_mode_enabled,
                "agent_runtime_fallback": settings.feishu_bot_ai_mode_enabled,
                "execution_boundary": "Agent Runtime -> Tool Router -> Tool -> 数据源/执行源；最终回复只由 Agent Runtime 生成",
                "identity_boundary": _agent_identity_boundary_payload(),
            },
            "admin_console": {"enabled": True},
            "ios_app": {"enabled": False, "status": "planned"},
        },
        "release_readiness": release_readiness,
        "companies": company_payloads,
        "total_counts": total_counts,
    }


def v5_entrypoint_status(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    try:
        active_feishu_app_count = _active_feishu_app_count(db, company_id=company_id)
        validated_feishu_app_count = _validated_feishu_app_count(db, company_id=company_id)
        cli_profiles = _feishu_cli_profiles(db, company_id=company_id)
        cli_readiness = _feishu_cli_readiness_check(cli_profiles)
        feishu_app_bootstrap_hint = _feishu_app_bootstrap_hint(
            cli_readiness,
            active_feishu_app_count=active_feishu_app_count,
            validated_feishu_app_count=validated_feishu_app_count,
        )
        apps = _feishu_entrypoint_apps(db, company_id=company_id)
        recent_events = _recent_feishu_entrypoint_events(db, company_id=company_id)
        gateway_runtime = _feishu_gateway_runtime_summary(db, company_id=company_id)
        recent_gateway_messages = _recent_feishu_gateway_messages(db, company_id=company_id)
        db_status = "ok"
        db_error = None
    except (OperationalError, ProgrammingError) as exc:
        active_feishu_app_count = 0
        validated_feishu_app_count = 0
        cli_readiness = {
            "key": "feishu_cli",
            "label": "飞书 CLI",
            "status": "unknown",
            "detail": "数据库不可用，无法按公司检查 CLI profile",
        }
        feishu_app_bootstrap_hint = {}
        apps = []
        recent_events = []
        gateway_runtime = _empty_gateway_runtime_summary()
        recent_gateway_messages = []
        db_status = "unavailable"
        db_error = str(exc)[:240]

    bot_status = "ready"
    bot_detail = "大飞哥可接收飞书事件并进入 Agent Runtime"
    if active_feishu_app_count == 0:
        bot_status = "blocked"
        bot_detail = "未找到 active 飞书 App，大飞哥无法接收事件"
    elif validated_feishu_app_count == 0:
        bot_status = "blocked"
        bot_detail = "已配置 active 飞书 App，但 App Secret 尚未通过 tenant token 校验"
    elif not settings.feishu_bot_ai_mode_enabled:
        bot_status = "attention"
        bot_detail = "AI 兜底未开启，自然语言无法进入 Agent Runtime"
    elif cli_readiness.get("status") == "blocked":
        bot_status = "blocked"
        bot_detail = cli_readiness.get("detail") or "飞书 CLI 不可用"

    return {
        "version": "v5",
        "status": {"database": db_status, "error": db_error} if db_error else {"database": db_status},
        "boundary": {
            "realtime_operation": "Message Gateway -> Agent Runtime -> Tool Router -> Tool -> 数据源/执行源 -> Agent Runtime -> Message Gateway",
            "sync_ingestion": "Sync Engine -> API Client -> Feishu -> PostgreSQL",
            "tool_contract": [
                "Tool 决定使用哪个数据源或执行源",
                "所有 Tool 返回结构化结果",
                "只有 Agent Runtime 可以生成最终回复",
            ],
            "data_or_execution_sources": [
                "MCP -> CLI -> Feishu",
                "API -> Feishu",
                "PostgreSQL",
                "Vector DB",
                "External Search",
            ],
            "identity_boundary": _agent_identity_boundary_payload(),
            "forbidden": [
                "Agent Runtime -> CLI",
                "Agent Runtime -> API",
                "Sync Engine -> MCP",
            ],
        },
        "entrypoints": [
            {
                "key": "feishu_bot",
                "label": "大飞哥机器人",
                "status": bot_status,
                "enabled": validated_feishu_app_count > 0,
                "detail": bot_detail,
                "execution_boundary": "Agent Runtime -> Tool Router -> Tool -> 数据源/执行源；最终回复只由 Agent Runtime 生成",
                "app_count": active_feishu_app_count,
                "validated_app_count": validated_feishu_app_count,
                "ai_mode_enabled": settings.feishu_bot_ai_mode_enabled,
                "agent_runtime_fallback": settings.feishu_bot_ai_mode_enabled,
                "cli_readiness": cli_readiness,
                "feishu_app_bootstrap_hint": feishu_app_bootstrap_hint,
                "apps": apps,
                "recent_events": recent_events,
                "gateway_runtime": gateway_runtime,
                "recent_gateway_messages": recent_gateway_messages,
                "next_action": _feishu_bot_entrypoint_next_action(
                    bot_status,
                    active_feishu_app_count=active_feishu_app_count,
                    validated_feishu_app_count=validated_feishu_app_count,
                    recent_event_count=len(recent_events),
                ),
                "identity_boundary": _agent_identity_boundary_payload(),
            },
            {
                "key": "admin_console",
                "label": "管理后台",
                "status": "ready",
                "enabled": True,
                "detail": "V5 经营后台已启用",
                "url": "/console",
                "next_action": "继续补齐资源同步、权限治理和经营报告操作面板",
            },
            {
                "key": "ios_app",
                "label": "iOS APP",
                "status": "planned",
                "enabled": False,
                "detail": "后续开发",
                "next_action": "先稳定大飞哥和管理后台，再设计移动端入口",
            },
        ],
    }


def _feishu_cli_readiness_check(cli_profiles: list[dict[str, str]] | None = None) -> dict[str, Any]:
    path = shutil.which("lark-cli")
    if not path:
        return {
            "key": "feishu_cli",
            "label": "飞书 CLI",
            "status": "blocked",
            "blocker": "feishu_cli_missing",
            "detail": "未在 PATH 中找到 lark-cli；实时飞书动作无法进入 CLI 执行层",
        }
    doctor = run_feishu_cli_doctor_offline(path)
    identity_status = _feishu_cli_identity_status(doctor.output)
    if doctor.returncode != 0:
        return {
            "key": "feishu_cli",
            "label": "飞书 CLI",
            "status": "blocked",
            "blocker": "feishu_cli_identity_unavailable",
            "detail": f"已找到 lark-cli：{path}，但离线健康检查未通过：{_feishu_cli_doctor_detail(doctor.output)}",
            "identity_status": identity_status,
        }
    profile_failures = []
    for item in cli_profiles or []:
        profile = item["profile"]
        profile_doctor = run_feishu_cli_doctor_offline(path, profile=profile)
        if profile_doctor.returncode != 0:
            profile_failures.append(f"{item['name']}:{profile}({_feishu_cli_doctor_detail(profile_doctor.output)})")
    if profile_failures:
        return {
            "key": "feishu_cli",
            "label": "飞书 CLI",
            "status": "blocked",
            "blocker": "feishu_cli_profile_unavailable",
            "detail": f"已找到 lark-cli：{path}，但公司 CLI profile 未通过：{'；'.join(profile_failures[:3])}",
        }
    return {
        "key": "feishu_cli",
        "label": "飞书 CLI",
        "status": "ready",
        "blocker": "",
        "detail": f"已找到 lark-cli：{path}；离线健康检查通过",
        "identity_status": identity_status,
    }


def _active_feishu_app_count(db: Session, company_id: UUID | None = None) -> int:
    query = select(func.count()).select_from(FeishuAppConfig).where(FeishuAppConfig.is_active.is_(True))
    if company_id:
        query = query.where(FeishuAppConfig.company_id == company_id)
    return db.scalar(query) or 0


def _validated_feishu_app_count(db: Session, company_id: UUID | None = None) -> int:
    query = (
        select(func.count())
        .select_from(FeishuAppConfig)
        .where(FeishuAppConfig.is_active.is_(True))
        .where(FeishuAppConfig.settings["credential_status"].astext == FEISHU_APP_CREDENTIAL_STATUS_VALID)
    )
    if company_id:
        query = query.where(FeishuAppConfig.company_id == company_id)
    return db.scalar(query) or 0


def _feishu_app_credential_status(app_config: FeishuAppConfig) -> str:
    settings_data = app_config.settings if isinstance(app_config.settings, dict) else {}
    status = settings_data.get("credential_status")
    return status if isinstance(status, str) and status else "unvalidated"


def _feishu_app_config_readiness_detail(
    *,
    active_feishu_app_count: int,
    validated_feishu_app_count: int,
) -> str:
    if active_feishu_app_count == 0:
        return "未找到 active 飞书 App 配置，大飞哥无法接收事件"
    if validated_feishu_app_count == 0:
        return f"已配置 {active_feishu_app_count} 个 active 飞书 App，但尚未通过 tenant token 校验"
    return f"已配置 {active_feishu_app_count} 个 active 飞书 App，其中 {validated_feishu_app_count} 个已校验"


def _feishu_cli_profiles(db: Session, company_id: UUID | None = None) -> list[dict[str, str]]:
    query = select(FeishuAppConfig).where(FeishuAppConfig.is_active.is_(True))
    if company_id:
        query = query.where(FeishuAppConfig.company_id == company_id)
    apps = db.scalars(query).all()
    profiles: list[dict[str, str]] = []
    seen: set[str] = set()
    for app_config in apps:
        profile = feishu_app_cli_profile(app_config)
        if not profile or profile in seen:
            continue
        seen.add(profile)
        profiles.append({"name": app_config.name, "profile": profile})
    return profiles


def _feishu_entrypoint_apps(db: Session, company_id: UUID | None = None) -> list[dict[str, Any]]:
    query = select(FeishuAppConfig).order_by(FeishuAppConfig.created_at.asc())
    if company_id:
        query = query.where(FeishuAppConfig.company_id == company_id)
    apps = db.scalars(query).all()
    return [
        {
            "id": str(app_config.id),
            "company_id": str(app_config.company_id),
            "name": app_config.name,
            "app_id": app_config.app_id,
            "is_active": app_config.is_active,
            "cli_profile": feishu_app_cli_profile(app_config),
            "credential_status": _feishu_app_credential_status(app_config),
            "credential_validated_at": (app_config.settings or {}).get("credential_validated_at"),
        }
        for app_config in apps
    ]


def _recent_feishu_entrypoint_events(db: Session, company_id: UUID | None = None, limit: int = 8) -> list[dict[str, Any]]:
    query = (
        select(WorkEvent)
        .where(WorkEvent.source == "feishu")
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit)
    )
    if company_id:
        query = query.where(WorkEvent.company_id == company_id)
    events = db.scalars(query).all()
    return [
        {
            "id": str(event.id),
            "company_id": str(event.company_id),
            "resource_id": str(event.resource_id) if event.resource_id else None,
            "event_type": event.event_type,
            "thread_id": event.thread_id,
            "title": event.title,
            "occurred_at": event.occurred_at.isoformat(),
            "vector_status": event.vector_status,
        }
        for event in events
    ]


def _empty_gateway_runtime_summary() -> dict[str, Any]:
    return {
        "recent_gateway_messages": 0,
        "agent_runtime_messages": 0,
        "tool_error_count": 0,
        "workflow_error_count": 0,
        "latest_error": None,
    }


def _feishu_gateway_runtime_summary(db: Session, company_id: UUID | None = None, limit: int = 100) -> dict[str, Any]:
    query = (
        select(AuditLog)
        .where(AuditLog.action == "gateway.feishu.message")
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    if company_id:
        query = query.where(AuditLog.company_id == company_id)
    logs = db.scalars(query).all()
    messages = [_gateway_message_runtime_payload(item) for item in logs]
    tool_error_count = sum(1 for item in messages for step in item["agent_runtime_tool_steps"] if _runtime_step_is_error(step))
    workflow_error_count = sum(
        1 for item in messages for step in item["agent_runtime_workflow_steps"] if _runtime_step_is_error(step)
    )
    latest_error = next((item for item in messages if _gateway_message_has_runtime_error(item)), None)
    return {
        "recent_gateway_messages": len(logs),
        "agent_runtime_messages": sum(1 for item in messages if item.get("used_agent_runtime") is True),
        "tool_error_count": tool_error_count,
        "workflow_error_count": workflow_error_count,
        "latest_error": _gateway_runtime_error_summary(latest_error) if latest_error else None,
    }


def _recent_feishu_gateway_messages(db: Session, company_id: UUID | None = None, limit: int = 12) -> list[dict[str, Any]]:
    query = (
        select(AuditLog)
        .where(AuditLog.action == "gateway.feishu.message")
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    if company_id:
        query = query.where(AuditLog.company_id == company_id)
    logs = db.scalars(query).all()
    return [_gateway_message_runtime_payload(item) for item in logs]


def _gateway_message_runtime_payload(item: AuditLog) -> dict[str, Any]:
    payload = item.payload if isinstance(item.payload, dict) else {}
    tool_steps = payload.get("agent_runtime_tool_steps") if isinstance(payload.get("agent_runtime_tool_steps"), list) else []
    workflow_steps = (
        payload.get("agent_runtime_workflow_steps")
        if isinstance(payload.get("agent_runtime_workflow_steps"), list)
        else []
    )
    return {
        "id": str(item.id),
        "company_id": str(item.company_id) if item.company_id else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "actor": item.actor,
        "message_id": payload.get("message_id") or item.target_id,
        "chat_id": payload.get("chat_id"),
        "status": payload.get("status"),
        "handled": payload.get("handled"),
        "reason": payload.get("reason"),
        "used_agent_runtime": payload.get("used_agent_runtime"),
        "final_answer_owner": payload.get("final_answer_owner"),
        "route_path": payload.get("route_path"),
        "route_label": payload.get("route_label"),
        "reply_mode_id": payload.get("reply_mode_id"),
        "reply_mode_label": payload.get("reply_mode_label"),
        "reply_mode_data_requirement": payload.get("reply_mode_data_requirement"),
        "thinking_notice_sent": payload.get("thinking_notice_sent"),
        "authorization_card_sent": payload.get("authorization_card_sent"),
        "user_identity_required": payload.get("user_identity_required"),
        "required_user_identity_resources": payload.get("user_identity_required_resources") or [],
        "authorization_owner_open_id": payload.get("user_identity_owner_open_id"),
        "authorization_action_count": payload.get("user_identity_action_count"),
        "agent_runtime_step_count": payload.get("agent_runtime_step_count"),
        "agent_runtime_tool_steps": tool_steps,
        "agent_runtime_workflow_steps": workflow_steps,
        "tool_summary": _gateway_tool_steps_summary(tool_steps),
        "workflow_summary": _gateway_workflow_steps_summary(workflow_steps),
        "gateway_chain": payload.get("gateway_chain") if isinstance(payload.get("gateway_chain"), list) else [],
    }


def _gateway_tool_steps_summary(steps: list[Any]) -> str:
    labels: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("business_tool") or step.get("name") or "").strip()
        source = str(step.get("data_source") or step.get("execution_source") or "").strip()
        status = str(step.get("status") or "").strip()
        if not name:
            continue
        suffix = " · ".join(item for item in [status, source] if item)
        labels.append(f"{name}（{suffix}）" if suffix else name)
    return " / ".join(labels)


def _gateway_workflow_steps_summary(steps: list[Any]) -> str:
    labels: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("route_label") or step.get("name") or "").strip()
        status = str(step.get("status") or "").strip()
        if not name:
            continue
        labels.append(f"{name}（{status}）" if status else name)
    return " / ".join(labels)


def _runtime_step_is_error(step: Any) -> bool:
    if not isinstance(step, dict):
        return False
    status = str(step.get("status") or "").strip().lower()
    return status in {"error", "failed", "failure", "blocked"}


def _gateway_message_has_runtime_error(item: dict[str, Any]) -> bool:
    return any(_runtime_step_is_error(step) for step in item["agent_runtime_tool_steps"]) or any(
        _runtime_step_is_error(step) for step in item["agent_runtime_workflow_steps"]
    )


def _gateway_runtime_error_summary(item: dict[str, Any]) -> dict[str, Any]:
    error_steps = [
        step
        for step in [*item["agent_runtime_tool_steps"], *item["agent_runtime_workflow_steps"]]
        if _runtime_step_is_error(step)
    ]
    return {
        "created_at": item.get("created_at"),
        "message_id": item.get("message_id"),
        "chat_id": item.get("chat_id"),
        "route_path": item.get("route_path"),
        "route_label": item.get("route_label"),
        "reply_mode_label": item.get("reply_mode_label"),
        "tool_summary": item.get("tool_summary"),
        "workflow_summary": item.get("workflow_summary"),
        "error": next((step.get("error") for step in error_steps if isinstance(step, dict) and step.get("error")), None),
        "final_answer_owner": item.get("final_answer_owner"),
    }


def _feishu_bot_entrypoint_next_action(
    status: str,
    *,
    active_feishu_app_count: int,
    validated_feishu_app_count: int,
    recent_event_count: int,
) -> str:
    if active_feishu_app_count == 0:
        return "先在系统配置中绑定公司飞书 App"
    if validated_feishu_app_count == 0:
        return "先刷新 tenant access token 校验 App Secret"
    if status == "blocked":
        return "先处理飞书 CLI / App 配置阻断"
    if status == "attention":
        return "开启大飞哥 AI 兜底后再验收自然语言入口"
    if recent_event_count == 0:
        return "把大飞哥加入业务群并发送测试消息，确认事件进入 WorkEvent"
    return "继续扩大业务群、审批、会议和知识资源覆盖"


def _agent_identity_boundary_payload() -> dict[str, Any]:
    return {
        "agent_model": "每个飞书用户一个专属 Agent",
        "tool_model": "9 个业务 Tool 全局共享，Agent 可以访问并调用所有 Tool",
        "tool_data_boundary_rule": "工具是全局共享的；数据是有边界的",
        "enterprise_resource_boundary": "App Identity + Company Scope + Role Scope",
        "user_resource_boundary": "User Identity + Resource Owner Authorization",
        "enterprise_resource_policy": "企业级资源继承大飞哥自建应用权限，并按公司范围和角色权限继续收紧",
        "user_resource_policy": "用户级资源只基于资源所有者本人授权；首次使用时按需引导授权",
        "digital_advisor_permission_policy": "只能收紧权限，不能突破飞书 App 或用户原始授权范围",
        "identity_permission_contract": identity_permission_contract(user_identity_required=False),
    }


def _feishu_cli_doctor_detail(output: str) -> str:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return (output or "无输出")[:240]
    if not isinstance(payload, dict):
        return "输出格式异常"
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return "未返回 checks"
    failed = [
        f"{item.get('name')}={item.get('status')}({item.get('message')})"
        for item in checks
        if isinstance(item, dict) and item.get("status") in {"fail", "warn"}
    ]
    return "；".join(failed[:3]) if failed else "doctor 返回非零退出码"


def _feishu_cli_identity_status(output: str) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return {}
    result: dict[str, dict[str, str]] = {}
    for item in checks:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name not in {"app_resolved", "bot_identity", "user_identity", "identity_ready"}:
            continue
        result[name] = {
            "status": str(item.get("status") or ""),
            "message": str(item.get("message") or ""),
        }
    return result


def _feishu_app_bootstrap_hint(
    cli_readiness: dict[str, Any],
    *,
    active_feishu_app_count: int,
    validated_feishu_app_count: int,
) -> dict[str, Any]:
    if active_feishu_app_count > 0 and validated_feishu_app_count > 0:
        return {
            "status": "ready",
            "required_action": "继续按公司独立 AppConfig 和 CLI Profile 接入后续公司",
        }
    identity_status = cli_readiness.get("identity_status") if isinstance(cli_readiness, dict) else None
    app_resolved = identity_status.get("app_resolved") if isinstance(identity_status, dict) else None
    message = str((app_resolved or {}).get("message") or "")
    app_id_match = re.search(r"app:\s*([A-Za-z0-9_\\-]+)", message)
    inferred_app_id = app_id_match.group(1) if app_id_match else None
    profile = None
    if inferred_app_id:
        profile = "v5-local-prod"
    if active_feishu_app_count == 0:
        required_action = (
            "CLI 已解析出 App ID；还需要把同一个 App Secret 写入 AppConfig 并执行 tenant token 校验"
            if inferred_app_id
            else "提供公司飞书 App ID / App Secret 后执行 V5 bootstrap；CLI bot identity 不能替代 API 同步层 AppConfig"
        )
        return {
            "status": "missing_app_config",
            "inferred_from_cli": bool(inferred_app_id),
            "app_id": inferred_app_id,
            "cli_profile": profile,
            "secret_required": True,
            "tenant_token_validation_required": True,
            "required_action": required_action,
            "sync_boundary": "Sync Engine -> API Client -> Feishu -> PostgreSQL",
        }
    return {
        "status": "credentials_unvalidated",
        "inferred_from_cli": bool(inferred_app_id),
        "app_id": inferred_app_id,
        "cli_profile": profile,
        "secret_required": True,
        "tenant_token_validation_required": True,
        "required_action": "调用 tenant-access-token 校验 App Secret；校验通过后继续按公司独立 AppConfig 和 CLI Profile 接入后续公司",
        "sync_boundary": "Sync Engine -> API Client -> Feishu -> PostgreSQL",
    }


def company_setting_payload(db: Session, company_id: UUID) -> dict[str, Any]:
    setting = db.scalar(select(CompanySetting).where(CompanySetting.company_id == company_id))
    if not setting:
        return {}
    return {"status": setting.status, "settings": setting.settings}


def company_counts(db: Session, company_id: UUID) -> dict[str, int]:
    return {
        "settings": count_company_items(db, CompanySetting, company_id=company_id),
        "departments": count_company_items(db, Department, company_id=company_id),
        "teams": count_company_items(db, Team, company_id=company_id),
        "permissions": count_company_items(db, Permission, company_id=company_id),
        "roles": count_company_items(db, Role, company_id=company_id),
        "users": count_v5_users(db, company_id=company_id),
        "resources": count_company_items(db, Resource, company_id=company_id),
        "resource_permissions": count_company_items(db, ResourcePermission, company_id=company_id),
        "work_events": count_company_items(db, WorkEvent, company_id=company_id),
    }


def count_company_items(db: Session, model: type[Any], company_id: UUID | None = None) -> int:
    query = select(func.count()).select_from(model)
    if company_id is not None:
        query = query.where(model.company_id == company_id)
    return db.scalar(query) or 0


def count_v5_users(db: Session, company_id: UUID | None = None) -> int:
    query = select(func.count(func.distinct(UserCompanyRole.user_id))).select_from(UserCompanyRole)
    if company_id is not None:
        query = query.where(UserCompanyRole.company_id == company_id)
    return db.scalar(query) or 0


def list_administration_users(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    query = (
        select(User, UserCompanyRole, Role, Company)
        .join(UserCompanyRole, UserCompanyRole.user_id == User.id)
        .join(Role, Role.id == UserCompanyRole.role_id)
        .join(Company, Company.id == UserCompanyRole.company_id)
        .order_by(Company.name.asc(), User.display_name.asc())
    )
    if company_id:
        query = query.where(UserCompanyRole.company_id == company_id)
    return {
        "items": [
            {
                "user_id": str(user.id),
                "company_id": str(company.id),
                "company_name": company.name,
                "display_name": user.display_name,
                "external_user_id": user.external_user_id,
                "email": user.email,
                "role": role.name,
                "scope_type": user_role.scope_type,
                "department_id": str(user_role.department_id) if user_role.department_id else None,
                "team_id": str(user_role.team_id) if user_role.team_id else None,
                "is_active": user_role.is_active,
                "settings": user_role.settings,
            }
            for user, user_role, role, company in db.execute(query).all()
        ]
    }


def list_administration_company_settings(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    query = (
        select(Company, CompanySetting)
        .outerjoin(CompanySetting, CompanySetting.company_id == Company.id)
        .order_by(Company.name.asc())
    )
    if company_id:
        query = query.where(Company.id == company_id)
    return {
        "items": [
            {
                "company_id": str(company.id),
                "company_name": company.name,
                "company_code": company.code,
                "status": setting.status if setting else getattr(company, "status", "active"),
                "settings": setting.settings if setting else {},
                "updated_at": setting.updated_at.isoformat() if setting else company.updated_at.isoformat(),
            }
            for company, setting in db.execute(query).all()
        ]
    }


def list_administration_departments(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    query = select(Department).order_by(Department.company_id.asc(), Department.name.asc())
    if company_id:
        query = query.where(Department.company_id == company_id)
    return {"items": [department_payload(item) for item in db.scalars(query).all()]}


def list_administration_teams(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    query = (
        select(Team, Department)
        .outerjoin(Department, Department.id == Team.department_id)
        .order_by(Team.company_id.asc(), Team.name.asc())
    )
    if company_id:
        query = query.where(Team.company_id == company_id)
    return {
        "items": [
            {
                "id": str(team.id),
                "company_id": str(team.company_id),
                "department_id": str(team.department_id) if team.department_id else None,
                "department_name": department.name if department else None,
                "name": team.name,
                "description": team.description,
                "payload": team.payload,
                "created_at": team.created_at.isoformat(),
            }
            for team, department in db.execute(query).all()
        ]
    }


def list_administration_roles(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    query = select(Role).order_by(Role.company_id.asc(), Role.name.asc())
    if company_id:
        query = query.where(Role.company_id == company_id)
    return {
        "items": [
            {
                "id": str(item.id),
                "company_id": str(item.company_id),
                "name": item.name,
                "description": item.description,
                "scope_type": item.scope_type,
                "permissions": item.permissions,
            }
            for item in db.scalars(query).all()
        ]
    }


def list_administration_permissions(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    query = select(Permission).order_by(Permission.company_id.asc(), Permission.permission_type.asc(), Permission.code.asc())
    if company_id:
        query = query.where(Permission.company_id == company_id)
    return {
        "items": [
            {
                "id": str(item.id),
                "company_id": str(item.company_id),
                "code": item.code,
                "name": item.name,
                "description": item.description,
                "permission_type": item.permission_type,
                "config_json": item.config_json,
                "created_at": item.created_at.isoformat(),
            }
            for item in db.scalars(query).all()
        ]
    }


def list_administration_resource_permissions(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    query = (
        select(ResourcePermission, Resource, Role, User)
        .join(Resource, Resource.id == ResourcePermission.resource_id)
        .outerjoin(Role, Role.id == ResourcePermission.role_id)
        .outerjoin(User, User.id == ResourcePermission.user_id)
        .order_by(Resource.resource_type.asc(), Resource.resource_name.asc())
    )
    if company_id:
        query = query.where(ResourcePermission.company_id == company_id)
    return {
        "items": [
            {
                "id": str(permission.id),
                "company_id": str(permission.company_id),
                "resource_id": str(resource.id),
                "resource_type": resource_to_v5_payload(resource)["resource_type"],
                "legacy_resource_type": resource.resource_type,
                "resource_name": resource.resource_name,
                "role": role.name if role else None,
                "user": user.display_name if user else None,
                "permission_level": permission.permission_level,
                "enabled": permission.enabled,
                "conditions_json": permission.conditions_json,
            }
            for permission, resource, role, user in db.execute(query).all()
        ]
    }


def preview_administration_resource_access(
    db: Session,
    *,
    company_id: UUID,
    resource_id: UUID,
    user_id: UUID | None = None,
    open_id: str | None = None,
    role: str | None = None,
    domains: list[str] | None = None,
    current_chat_id: str | None = None,
) -> dict[str, Any]:
    if user_id or open_id:
        principal = build_principal_for_user(
            db,
            company_id=company_id,
            user_id=user_id,
            open_id=open_id,
            current_chat_id=current_chat_id,
        )
    else:
        principal = AccessPrincipal(
            company_id=company_id,
            role=role or "employee",
            domains=tuple(domains or []),
            current_chat_id=current_chat_id,
        )
    return access_preview_for_resource(db, resource_id=resource_id, principal=principal)


def department_payload(item: Department) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "company_id": str(item.company_id),
        "name": item.name,
        "description": item.description,
        "parent_department_id": str(item.parent_department_id) if item.parent_department_id else None,
        "external_id": item.external_id,
        "payload": item.payload,
        "created_at": item.created_at.isoformat(),
    }


def _target_companies(db: Session, company_id: UUID | None) -> list[Company]:
    query = select(Company).order_by(Company.created_at.asc())
    if company_id:
        query = query.where(Company.id == company_id)
    return list(db.scalars(query).all())


def _ensure_default_launch_company(db: Session) -> tuple[Company, bool]:
    company = db.scalar(select(Company).where(Company.code == "gaustek"))
    if company:
        return company, False
    company = Company(
        name="固势 (Gaustek)",
        code="gaustek",
        status="active",
        metadata_json={
            "source": "v5_local_seed",
            "purpose": "launch_company_placeholder",
            "requires_real_feishu_app_config": True,
        },
    )
    db.add(company)
    db.flush()
    return company, True


def _ensure_default_owner_agent(db: Session, company: Company) -> int:
    owner_open_id = _default_owner_open_id()
    if not owner_open_id:
        return 0
    existing = db.scalar(
        select(BotUserAccess).where(BotUserAccess.company_id == company.id).where(BotUserAccess.open_id == owner_open_id)
    )
    settings_payload = {
        "source": "v5_local_seed_owner_agent",
        "permission_domains": ["all"],
        "allowed_resources": [],
        "agent_profile": {
            "status": "active",
            "scope": "company",
            "entrypoint": "feishu_bot",
            "activation_source": "local_seed",
            "requires_feishu_app_config": True,
        },
        "user_identity_authorizations": default_user_identity_authorizations(open_id=owner_open_id),
    }
    if existing:
        existing.display_name = existing.display_name or "Joon"
        existing.role = "owner"
        existing.access_scope = "company"
        existing.is_active = True
        existing.settings = json_safe({**settings_payload, **(existing.settings or {})})
        return 0
    db.add(
        BotUserAccess(
            company_id=company.id,
            open_id=owner_open_id,
            display_name="Joon",
            role="owner",
            access_scope="company",
            is_active=True,
            settings=json_safe(settings_payload),
        )
    )
    return 1


def _default_owner_open_id() -> str | None:
    admin_open_ids = [item.strip() for item in settings.feishu_bot_admin_open_ids.split(",") if item.strip()]
    if admin_open_ids:
        return admin_open_ids[0]
    if settings.feishu_default_receive_id_type == "open_id" and settings.feishu_default_receive_id:
        return settings.feishu_default_receive_id.strip()
    return None


def _ensure_company_setting(db: Session, company: Company) -> int:
    item = db.scalar(select(CompanySetting).where(CompanySetting.company_id == company.id))
    if item:
        settings = item.settings or {}
        if settings.get("architecture") != "v5":
            settings = {**settings, "architecture": "v5", "legacy_company_code": company.code}
            item.settings = json_safe(settings)
        return 0
    db.add(
        CompanySetting(
            company_id=company.id,
            status="active",
            settings={"source": "v5_administration", "architecture": "v5", "legacy_company_code": company.code},
        )
    )
    return 1


def _ensure_default_roles(db: Session, company: Company) -> tuple[dict[str, Role], int]:
    existing = {item.name: item for item in db.scalars(select(Role).where(Role.company_id == company.id)).all()}
    created = 0
    for spec in DEFAULT_V5_ROLES:
        role = existing.get(spec["name"])
        if role:
            if not role.description:
                role.description = spec["description"]
            role.scope_type = role.scope_type or spec["scope_type"]
            role.permissions = json_safe({**spec["permissions"], **(role.permissions or {})})
            continue
        role = Role(
            company_id=company.id,
            name=spec["name"],
            description=spec["description"],
            scope_type=spec["scope_type"],
            permissions=json_safe(spec["permissions"]),
        )
        db.add(role)
        existing[role.name] = role
        created += 1
    db.flush()
    return existing, created


def _ensure_default_permissions(db: Session, company: Company) -> int:
    existing = {item.code: item for item in db.scalars(select(Permission).where(Permission.company_id == company.id))}
    created = 0
    for spec in DEFAULT_V5_PERMISSIONS:
        permission = existing.get(spec["code"])
        if permission:
            if not permission.description:
                permission.description = spec["description"]
            permission.config_json = json_safe({**spec["config_json"], **(permission.config_json or {})})
            continue
        db.add(
            Permission(
                company_id=company.id,
                code=spec["code"],
                name=spec["name"],
                description=spec["description"],
                permission_type=spec["permission_type"],
                config_json=json_safe(spec["config_json"]),
            )
        )
        created += 1
    return created


def _migrate_bot_users(db: Session, company: Company, role_map: dict[str, Role]) -> tuple[int, int]:
    created_users = 0
    created_user_roles = 0
    bot_users = db.scalars(select(BotUserAccess).where(BotUserAccess.company_id == company.id)).all()
    for bot_user in bot_users:
        user, user_created = _find_or_create_user_from_bot_access(db, bot_user)
        if user_created:
            created_users += 1
        role_name = legacy_bot_role_to_v5_role(bot_user.role)
        role = role_map.get(role_name) or role_map["employee"]
        existing_role = db.scalar(
            select(UserCompanyRole)
            .where(UserCompanyRole.user_id == user.id)
            .where(UserCompanyRole.company_id == company.id)
            .where(UserCompanyRole.role_id == role.id)
        )
        if existing_role:
            continue
        db.add(
            UserCompanyRole(
                user_id=user.id,
                company_id=company.id,
                role_id=role.id,
                scope_type=legacy_scope_to_v5_scope(bot_user.access_scope),
                is_active=bot_user.is_active,
                settings={
                    "source": "bot_user_access",
                    "legacy_bot_user_access_id": str(bot_user.id),
                    "legacy_role": bot_user.role,
                    "legacy_access_scope": bot_user.access_scope,
                    "permission_domains": (bot_user.settings or {}).get("permission_domains") or [],
                    "allowed_resources": (bot_user.settings or {}).get("allowed_resources") or [],
                },
            )
        )
        created_user_roles += 1
    return created_users, created_user_roles


def _find_or_create_user_from_bot_access(db: Session, bot_user: BotUserAccess) -> tuple[User, bool]:
    user = db.scalar(select(User).where(User.external_user_id == bot_user.open_id))
    if user:
        return user, False
    settings = bot_user.settings or {}
    user = User(
        external_user_id=bot_user.open_id,
        display_name=bot_user.display_name or bot_user.open_id,
        email=settings.get("email"),
        avatar_url=settings.get("avatar_url"),
        settings={
            "source": "bot_user_access",
            "legacy_bot_user_access_id": str(bot_user.id),
            "feishu_open_id": bot_user.open_id,
        },
    )
    db.add(user)
    db.flush()
    return user, True


def _migrate_feishu_resources(db: Session, company: Company) -> int:
    created = 0
    try:
        transaction = db.begin_nested() if hasattr(db, "begin_nested") else nullcontext()
        with transaction:
            legacy_items = (
                db.execute(
                    text(
                        """
                        select id, company_id, app_config_id, resource_type, external_id, name, sync_enabled, settings
                        from feishu_resources
                        where company_id = :company_id
                        """
                    ),
                    {"company_id": company.id},
                )
                .mappings()
                .all()
            )
    except (OperationalError, ProgrammingError):
        return 0
    for legacy in legacy_items:
        resource_id, resource_sub_id = _legacy_resource_identity(legacy)
        legacy_resource_type = _legacy_value(legacy, "resource_type")
        existing = db.scalar(
            select(Resource)
            .where(Resource.company_id == company.id)
            .where(Resource.platform == "feishu")
            .where(Resource.resource_type == legacy_resource_type)
            .where(Resource.resource_id == resource_id)
            .where(
                Resource.resource_sub_id == resource_sub_id
                if resource_sub_id is not None
                else Resource.resource_sub_id.is_(None)
            )
        )
        if existing:
            continue
        db.add(
            Resource(
                company_id=company.id,
                platform="feishu",
                resource_type=legacy_resource_type,
                resource_name=_legacy_value(legacy, "name") or _legacy_value(legacy, "external_id"),
                resource_id=resource_id,
                resource_sub_id=resource_sub_id,
                sync_mode=(_legacy_value(legacy, "settings") or {}).get("sync_mode") or "manual",
                permission_level=(_legacy_value(legacy, "settings") or {}).get("permission_level") or "company",
                enabled=bool(_legacy_value(legacy, "sync_enabled")),
                config_json={
                    "source": "feishu_resources",
                    "legacy_settings": json_safe(_legacy_value(legacy, "settings") or {}),
                },
                legacy_feishu_resource_id=_legacy_value(legacy, "id"),
                app_config_id=_legacy_value(legacy, "app_config_id"),
            )
        )
        created += 1
    return created


def _legacy_resource_identity(legacy: Any) -> tuple[str, str | None]:
    settings = _legacy_value(legacy, "settings") or {}
    if _legacy_value(legacy, "resource_type") == "mail_folder":
        user_mailbox_id = settings.get("user_mailbox_id")
        folder_id = settings.get("folder_id")
        if user_mailbox_id and folder_id:
            return str(user_mailbox_id), str(folder_id)
    return _legacy_value(legacy, "external_id"), None


def _legacy_value(legacy: Any, key: str) -> Any:
    if isinstance(legacy, Mapping):
        return legacy.get(key)
    return getattr(legacy, key, None)


def _register_feishu_apps_as_bot_resources(db: Session, company: Company) -> int:
    created = 0
    apps = db.scalars(select(FeishuAppConfig).where(FeishuAppConfig.company_id == company.id)).all()
    for app_config in apps:
        existing = db.scalar(
            select(Resource)
            .where(Resource.company_id == company.id)
            .where(Resource.platform == "feishu")
            .where(Resource.resource_type == "bot")
            .where(Resource.resource_id == app_config.app_id)
        )
        if existing:
            continue
        db.add(
            Resource(
                company_id=company.id,
                platform="feishu",
                resource_type="bot",
                resource_name=app_config.name,
                resource_id=app_config.app_id,
                sync_mode="realtime",
                permission_level="company",
                enabled=app_config.is_active,
                config_json={
                    "source": "feishu_app_configs",
                    "app_config_id": str(app_config.id),
                    "settings": json_safe(app_config.settings or {}),
                },
                app_config_id=app_config.id,
            )
        )
        created += 1
    return created


def _ensure_owner_resource_permissions(db: Session, company: Company, role_map: dict[str, Role]) -> int:
    owner_role = role_map.get("owner")
    if not owner_role:
        return 0
    created = 0
    resources = db.scalars(select(Resource).where(Resource.company_id == company.id)).all()
    for resource in resources:
        existing = db.scalar(
            select(ResourcePermission)
            .where(ResourcePermission.company_id == company.id)
            .where(ResourcePermission.resource_id == resource.id)
            .where(ResourcePermission.role_id == owner_role.id)
        )
        if existing:
            continue
        db.add(
            ResourcePermission(
                company_id=company.id,
                resource_id=resource.id,
                role_id=owner_role.id,
                permission_level="admin",
                conditions_json={"source": "v5_administration"},
                enabled=True,
            )
        )
        created += 1
    return created
