from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import BotUserAccess, FeishuAppConfig
from app.services.audit import write_audit_log
from app.services.feishu import command_parser
from app.services.permissions import question_allowed_by_domains
from app.services.tools.base import SHARED_TOOL_COUNT
from app.services.user_identity_authorizations import default_user_identity_authorizations


@dataclass(frozen=True)
class BotIdentity:
    open_id: str | None
    role: str
    access_scope: str
    display_name: str | None = None
    source: str = "default"
    domains: tuple[str, ...] = ()
    allowed_resources: tuple[str, ...] = ()
    email: str | None = None

    @property
    def can_query_company(self) -> bool:
        return self.role in {"owner", "admin"} and self.access_scope in {"company", "all"}

    def can_query_domains(self, question: str) -> bool:
        return self.access_scope in {"domain", "department", "project"} and question_allowed_by_domains(
            question,
            list(self.domains),
        )

    def can_query_approvals(self) -> bool:
        approval_domains = {"finance", "approval", "hr", "admin", "supply_chain", "sales"}
        return self.can_query_company or bool(approval_domains & set(self.domains))

    @property
    def label(self) -> str:
        role_labels = {
            "owner": "老板 / 系统所有者",
            "admin": "管理员",
            "manager": "负责人",
            "member": "成员",
            "guest": "访客",
        }
        scope_labels = {
            "all": "全库",
            "company": "公司级",
            "department": "部门级",
            "project": "项目级",
            "domain": "业务域",
            "personal": "本人相关",
            "chat": "当前会话",
            "public": "公开知识",
        }
        return f"{role_labels.get(self.role, self.role)}，{scope_labels.get(self.access_scope, self.access_scope)}权限"


def member_help_text(identity: BotIdentity) -> str:
    domain_line = f"\n已授权业务域：{', '.join(identity.domains)}。" if identity.domains else ""
    resources_line = f"\n可用资源：{'、'.join(identity.allowed_resources[:6])}。" if identity.allowed_resources else ""
    return f"""我是老板的企业数字助理，也可以作为你的工作助理回答已授权信息。

我识别到你的身份：{identity.label}。
{domain_line}{resources_line}

你可以问：
- 最近这个会话聊了什么？
- 这个群里有哪些待跟进事项？
- 刚才讨论的重点是什么？
- 公司公开知识库里某个流程怎么做？

你的当前权限不能查询老板邮箱、全公司日报、薪资、财务、其他群聊或全局数据库。"""


def identity_reply(identity: BotIdentity) -> str:
    if identity.can_query_company:
        name = identity.display_name or "老板"
        return (
            f"老板，我记得。你是{name}，也是这套数字参谋的系统所有者。"
            f"当前识别权限：{identity.label}。我可以帮你查全局工作事件、"
            "飞书邮箱、日报、待办、风险、会议、飞书历史消息，以及后续接入的企业知识库。"
        )
    if identity.domains:
        resources = "、".join(identity.allowed_resources[:8]) or "已授权业务域资料"
        return (
            f"我识别到你的身份：{identity.label}。已授权业务域：{', '.join(identity.domains)}。"
            f"你可以查询：{resources}。未授权的老板邮箱、全公司数据库、薪资财务明细和其他群聊不会向你开放。"
        )
    return (
        f"我识别到你的身份：{identity.label}。我可以回答当前会话和已授权知识库范围内的问题；"
        "全公司数据库、邮箱、薪资、财务和其他群聊信息不会向你开放。"
    )


def bot_identity_reply(identity: BotIdentity) -> str:
    owner_line = "你是我的系统所有者，我会按老板权限服务你。" if identity.can_query_company else "我会按你当前权限回答。"
    return (
        "我是大飞哥，你的企业数字助理，不是普通闲聊机器人。\n"
        "我的职责是帮你同步和理解飞书、邮箱、审批、通讯录、会议、任务、日报和企业知识库。\n"
        f"{owner_line}"
    )


def online_status_reply(identity: BotIdentity) -> str:
    if identity.can_query_company:
        return (
            "老板，我现在在线。你能收到这条回复，说明飞书长连接和机器人回复链路正在工作。"
            "如果管理后台打不开，那是本地 API / Docker 服务问题，不代表我在飞书侧离线。"
        )
    return "我现在在线。你可以继续问当前会话或你已授权业务域内的问题。"


def greeting_reply(identity: BotIdentity) -> str:
    if identity.can_query_company:
        return "老板，我在。你可以直接问公司重点、审批、邮件、风险、通讯录或某个项目。"
    return "我在。你可以问当前会话重点、待办，或你已授权业务域内的信息。"


def permission_denied_reply(identity: BotIdentity, resource_name: str) -> str:
    domains = f"\n已授权业务域：{', '.join(identity.domains)}。" if identity.domains else ""
    return (
        f"这部分我不能向你开放：{resource_name}。\n"
        f"我识别到你的身份是{identity.label}。{domains}\n"
        "我只能回答当前会话、公开知识库或你已授权业务域范围内的信息。"
    )


def mail_capability_reply(identity: BotIdentity) -> str:
    if identity.can_query_company:
        return "可以。你可以直接问“最近邮件有什么重点”“查看最近一封邮件”“同步邮箱”，我会优先查飞书邮箱同步进来的数据。"
    return permission_denied_reply(identity, "邮件")


def get_sender_identity(
    db: Session,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
    *,
    open_id: str | None = None,
) -> BotIdentity:
    open_id = open_id or command_parser.get_sender_open_id(payload)
    if not open_id:
        return BotIdentity(open_id=None, role="guest", access_scope="public", source="missing_sender")
    access = db.scalar(
        select(BotUserAccess)
        .where(BotUserAccess.company_id == app_config.company_id)
        .where(BotUserAccess.open_id == open_id)
        .where(BotUserAccess.is_active.is_(True))
    )
    if access:
        settings_data = getattr(access, "settings", None) or {}
        return BotIdentity(
            open_id=open_id,
            role=access.role,
            access_scope=access.access_scope,
            display_name=access.display_name,
            source="database",
            domains=tuple(settings_data.get("permission_domains") or []),
            allowed_resources=tuple(settings_data.get("allowed_resources") or []),
            email=settings_data.get("email"),
        )
    if is_admin_sender(open_id):
        return BotIdentity(open_id=open_id, role="owner", access_scope="company", source="env", domains=("all",))
    if _ensure_default_employee_agent_access(db, app_config=app_config, payload=payload, open_id=open_id):
        return BotIdentity(open_id=open_id, role="member", access_scope="personal", source="default_employee_agent")
    return BotIdentity(open_id=open_id, role="member", access_scope="chat", source="default_chat_member")


def _ensure_default_employee_agent_access(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    payload: dict[str, Any],
    open_id: str,
) -> bool:
    if not hasattr(db, "add") or not hasattr(db, "flush"):
        return False
    now = datetime.now(UTC).isoformat()
    first_chat_id = _payload_message_value(payload, "chat_id")
    first_message_id = _payload_message_value(payload, "message_id")
    access = BotUserAccess(
        company_id=app_config.company_id,
        open_id=open_id,
        display_name=_sender_display_name(payload),
        role="member",
        access_scope="personal",
        is_active=True,
        settings={
            "source": "default_employee_agent",
            "permission_domains": [],
            "allowed_resources": [],
            "agent_profile": {
                "status": "active",
                "scope": "personal",
                "entrypoint": "feishu_bot",
                "activated_at": now,
                "first_chat_id": first_chat_id,
                "first_message_id": first_message_id,
            },
            "user_identity_authorizations": default_user_identity_authorizations(open_id=open_id, created_at=now),
        },
    )
    db.add(access)
    write_audit_log(
        db,
        action="agent.employee.activate",
        company_id=app_config.company_id,
        actor=open_id,
        target_type="employee_agent",
        target_id=open_id,
        payload={
            "status": "success",
            "agent_type": "employee_personal_agent",
            "agent_owner_open_id": open_id,
            "agent_owner_display_name": _sender_display_name(payload),
            "agent_entrypoint": "feishu_bot",
            "agent_scope": "personal",
            "first_chat_id": first_chat_id,
            "first_message_id": first_message_id,
            "activated_at": now,
            "shared_business_tool_count": SHARED_TOOL_COUNT,
            "enterprise_scope_status": "available",
            "enterprise_resources_available_after_agent_created": True,
            "user_identity_access_model": "bundle_authorization",
            "user_identity_bundle_status": "not_authorized",
            "digital_advisor_can_only_tighten": True,
            "can_escalate_original_permissions": False,
        },
    )
    db.flush()
    return True

def _sender_display_name(payload: dict[str, Any]) -> str | None:
    sender = payload.get("event", {}).get("sender", {}) if isinstance(payload.get("event"), dict) else {}
    for key in ("name", "display_name", "sender_name"):
        value = sender.get(key) if isinstance(sender, dict) else None
        if value:
            return str(value)
    return None


def _payload_message_value(payload: dict[str, Any], key: str) -> str | None:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    value = message.get(key)
    return str(value) if value else None


def is_admin_sender(open_id: str | None) -> bool:
    allowed = {
        item.strip()
        for item in settings.feishu_bot_admin_open_ids.split(",")
        if item.strip()
    }
    if not allowed and settings.feishu_default_receive_id_type == "open_id":
        allowed.add(settings.feishu_default_receive_id or "")
    return bool(open_id and open_id in allowed)


def identity_payload(identity: BotIdentity) -> dict[str, Any]:
    return {
        "open_id": identity.open_id,
        "role": identity.role,
        "access_scope": identity.access_scope,
        "display_name": identity.display_name,
        "source": identity.source,
        "domains": list(identity.domains),
        "allowed_resources": list(identity.allowed_resources),
        "email": identity.email,
    }


def identity_from_payload(payload: dict[str, Any]) -> BotIdentity:
    return BotIdentity(
        open_id=payload.get("open_id"),
        role=str(payload.get("role") or "member"),
        access_scope=str(payload.get("access_scope") or "chat"),
        display_name=payload.get("display_name"),
        source=str(payload.get("source") or "async"),
        domains=tuple(payload.get("domains") or ()),
        allowed_resources=tuple(payload.get("allowed_resources") or ()),
        email=payload.get("email"),
    )
