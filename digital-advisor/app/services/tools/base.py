from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.agent.policies import BotActor


class ToolProvider(StrEnum):
    LOCAL = "local"
    FEISHU_API = "feishu_api"
    FEISHU_MCP = "feishu_mcp"
    REPORT = "report"
    DEVOPS = "devops"


class ToolExecutionStatus(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"





SHARED_TOOL_COUNT = 9


TOOL_ACCESS_POLICY = "all_business_tools_shared"
TOOL_SHARING_MODEL = "shared_business_tools_per_employee_agent"
DATA_PERMISSION_MODEL = "identity_scoped_tighten_only"
DATA_BOUNDARY_POLICY = "tool_global_data_identity_bounded"
DIGITAL_ADVISOR_PERMISSION_POLICY = "tighten_only"
ENTERPRISE_IDENTITY_CONSTRAINTS = ("app_identity", "company_scope", "role_scope")
USER_IDENTITY_CONSTRAINTS = ("resource_owner_authorization",)
USER_IDENTITY_BUNDLE_RESOURCE = "user_identity_bundle"
USER_IDENTITY_RESOURCES = ("personal_feishu", "external_mail", "personal_dingtalk", "personal_wechat")
ENTERPRISE_IDENTITY_RESOURCE_OWNER = "feishu_custom_app_da_fei_ge"
USER_IDENTITY_AUTHORIZATION_POLICY = "resource_owner_authorized_only"



@dataclass(frozen=True)
class ToolDefinition:
    name: str
    provider: ToolProvider
    required_permissions: tuple[str, ...]
    audit_action: str
    family: str | None = None
    capabilities: list[str] = field(default_factory=list)
    supports_write: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class ToolContext:
    db: Session
    company_id: UUID
    actor: BotActor
    chat_id: str | None = None
    cli_profile: str | None = None


@dataclass(frozen=True)
class ToolRequest:
    tool_name: str
    question: str
    normalized_command: str
    provider_preference: ToolProvider = ToolProvider.LOCAL
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    provider: ToolProvider
    answer: str
    status: ToolExecutionStatus = ToolExecutionStatus.SUCCESS
    error: str | None = None
    structured_result: dict[str, Any] = field(default_factory=dict)
    data_source: str | None = None
    execution_source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def identity_permission_contract(*, user_identity_required: bool) -> dict[str, Any]:
    return {
        "agent_model": "per_user_personal_agent",
        "tool_model": "global_shared_business_tools",
        "tool_access_policy": TOOL_ACCESS_POLICY,
        "tool_sharing_model": TOOL_SHARING_MODEL,
        "shared_business_tools": ["ApprovalTool", "KnowledgeTool", "BitableTool", "ChatTool", "CalendarTool", "MeetingTool", "ReportTool", "AutomationTool", "PeopleTool"],
        "agent_can_call_all_business_tools": True,
        "data_permission_model": DATA_PERMISSION_MODEL,
        "data_boundary_policy": DATA_BOUNDARY_POLICY,
        "tool_data_boundary_rule": {
            "tools": "global_shared_capabilities",
            "data": "bounded_by_app_identity_and_user_identity",
            "digital_advisor_can_only_tighten": True,
        },
        "enterprise_resource_boundary": {
            "identity": "app_identity",
            "resource_owner": ENTERPRISE_IDENTITY_RESOURCE_OWNER,
            "constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
            "app_identity_permissions_required": True,
            "company_scope_required": True,
            "role_scope_required": True,
            "can_exceed_feishu_app_permissions": False,
        },
        "user_resource_boundary": {
            "identity": "resource_owner_identity",
            "authorization_model": "bundle_authorization",
            "authorization_resource": USER_IDENTITY_BUNDLE_RESOURCE,
            "supported_resources": list(USER_IDENTITY_RESOURCES),
            "constraints": list(USER_IDENTITY_CONSTRAINTS),
            "authorization_policy": USER_IDENTITY_AUTHORIZATION_POLICY,
            "owner_authorized_only": True,
            "required": user_identity_required,
            "can_exceed_original_authorization": False,
        },
        "digital_advisor_permission_policy": DIGITAL_ADVISOR_PERMISSION_POLICY,
        "permission_enforcement": {
            "enterprise_resources": "app_identity_company_scope_role_scope",
            "user_resources": "resource_owner_authorization",
            "digital_advisor_can_only_tighten": True,
            "can_escalate_original_permissions": False,
        },
    }
