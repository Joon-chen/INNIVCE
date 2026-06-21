"""V5 Feishu capability boundary.

Business modules should import Feishu capabilities from this package only.
SDK-backed and Raw HTTP Feishu capabilities live under this package; business
modules must not import SDK clients or Raw HTTP helpers directly.
"""

from app.services.feishu.client import (
    FEISHU_ROUTE_RULES,
    FeishuClient,
    FeishuClientMode,
    get_feishu_app_or_404,
    ingest_feishu_event,
    ingest_feishu_message,
    route_for_feishu_api,
    route_rule_to_dict,
    verify_feishu_token,
)
from app.services.feishu.approval import FeishuApprovalService
from app.services.feishu.bitable import FeishuBitableService
from app.services.feishu.calendar import FeishuCalendarService
from app.services.feishu.contact import FeishuContactService
from app.services.feishu.drive import FeishuDriveService
from app.services.feishu.im import FeishuImService
from app.services.feishu.mail import FeishuMailService
from app.services.feishu.meeting import FeishuMeetingService
from app.services.feishu.resources import (
    discover_feishu_resources,
    extract_resource_candidates_from_payload,
)
from app.services.feishu.sync import (
    get_feishu_sync_plan,
    probe_feishu_capabilities,
    sync_feishu_information,
)
from app.services.feishu.task import FeishuTaskService


def __getattr__(name: str):
    if name in {"handle_feishu_command", "handle_feishu_command_result"}:
        from app.services.feishu import commands

        return getattr(commands, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FEISHU_ROUTE_RULES",
    "FeishuApprovalService",
    "FeishuBitableService",
    "FeishuCalendarService",
    "FeishuClient",
    "FeishuClientMode",
    "FeishuContactService",
    "FeishuDriveService",
    "FeishuImService",
    "FeishuMailService",
    "FeishuMeetingService",
    "FeishuTaskService",
    "discover_feishu_resources",
    "extract_resource_candidates_from_payload",
    "get_feishu_app_or_404",
    "get_feishu_sync_plan",
    "handle_feishu_command",
    "handle_feishu_command_result",
    "ingest_feishu_event",
    "ingest_feishu_message",
    "probe_feishu_capabilities",
    "route_for_feishu_api",
    "route_rule_to_dict",
    "sync_feishu_information",
    "verify_feishu_token",
]
