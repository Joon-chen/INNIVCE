from typing import Any

from app.services.v5_resources import normalize_v5_resource_type
from app.services.v5_sync_strategy import sync_action_for_resource


EXECUTABLE_RESOURCE_SYNC_ACTIONS = {"realtime_event", "master_data_index", "document_index_only", "knowledge_vectorize"}
DEFERRED_RESOURCE_SYNC_ACTIONS = {"memory_extract_later"}
REDACTION_MARKERS = ("[PHONE_REDACTED]", "[EMAIL_REDACTED]", "[TOKEN_REDACTED]", "[SECRET_REDACTED]", "_REDACTED]")
ACCESS_BLOCKED_STATUSES = {"bot_not_in_chat", "access_denied", "requires_user_authorization"}


def sync_decision_for_resource(resource: Any, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or {}
    action = sync_action_for_resource(
        getattr(resource, "resource_type", None),
        getattr(resource, "resource_id", None),
        large_document_mode=str(policy.get("large_document_mode") or "index_only"),
        bitable_mode=str(policy.get("bitable_mode") or "master_data_index"),
    )
    normalized_type = normalize_v5_resource_type(
        getattr(resource, "resource_type", None),
        getattr(resource, "resource_id", None),
    )
    allowed_types = set(policy.get("resource_types") or [])
    type_allowed = (
        not allowed_types
        or normalized_type in allowed_types
        or getattr(resource, "resource_type", None) in allowed_types
    )
    enabled = bool(getattr(resource, "enabled", True))
    platform = str(getattr(resource, "platform", "") or "")
    redacted_identifier = _has_redacted_identifier(resource)
    access_status = _resource_access_status(resource)
    identifier_issue = _resource_identifier_issue(resource, normalized_type)
    status, executable, reason = _decision_state(
        enabled=enabled,
        platform=platform,
        redacted_identifier=redacted_identifier,
        access_status=access_status,
        identifier_issue=identifier_issue,
        type_allowed=type_allowed,
        action=action["action"],
        reason=action["reason"],
    )
    return {
        "resource_type": normalized_type,
        "legacy_resource_type": getattr(resource, "resource_type", None),
        "data_layer": action["data_layer"],
        "sync_action": action["action"],
        "query_path": action.get("query_path", "manual_review"),
        "extract_items": bool(action.get("extract_items")),
        "vectorize": action.get("vectorize", "none"),
        "authorization": action.get("authorization"),
        "status": status,
        "executable": executable,
        "auto_sync_allowed": executable and type_allowed,
        "type_allowed": type_allowed,
        "reason": reason,
        "access_status": access_status,
        "identifier_issue": identifier_issue,
        "has_redacted_identifier": redacted_identifier,
        "next_action": _next_action(
            status=status,
            sync_action=action["action"],
            reason=reason,
            access_status=access_status,
            identifier_issue=identifier_issue,
        ),
    }


def _decision_state(
    *,
    enabled: bool,
    platform: str,
    redacted_identifier: bool,
    access_status: str | None,
    identifier_issue: dict[str, str] | None,
    type_allowed: bool,
    action: str,
    reason: str,
) -> tuple[str, bool, str]:
    if not enabled:
        return "disabled", False, "资源未启用。"
    if platform and platform != "feishu":
        return "unsupported", False, "V5 当前只同步飞书资源。"
    if redacted_identifier:
        return "manual_review_required", False, "资源标识包含脱敏标记，不能调用飞书 API；系统需要重新自动发现真实资源。"
    if identifier_issue:
        if identifier_issue["code"] == "requires_user_authorization":
            return "access_blocked", False, identifier_issue["reason"]
        return "manual_review_required", False, identifier_issue["reason"]
    if access_status == "bot_not_in_chat":
        return "access_blocked", False, "机器人不在该群，无法读取群消息。"
    if access_status == "requires_user_authorization":
        return "access_blocked", False, "该资源需要用户授权或系统自动发现到具体资源，不能仅用通用能力标识直接同步。"
    if access_status in ACCESS_BLOCKED_STATUSES:
        return "access_blocked", False, "飞书资源访问受限，需要先处理授权或成员关系。"
    if not type_allowed:
        return "policy_excluded", False, "当前自动同步策略未包含该资源类型。"
    if action == "skip":
        return "skipped", False, reason
    if action == "manual_review_required":
        return "manual_review_required", False, reason
    if action in DEFERRED_RESOURCE_SYNC_ACTIONS:
        return "manual_review_required", False, "该动作需要知识切片或长期记忆管道完成后再启用。"
    if action in EXECUTABLE_RESOURCE_SYNC_ACTIONS:
        return "ready", True, reason
    return "unsupported", False, reason


def _next_action(
    *,
    status: str,
    sync_action: str,
    reason: str,
    access_status: str | None = None,
    identifier_issue: dict[str, str] | None = None,
) -> str:
    if status == "ready":
        if sync_action == "realtime_event":
            return "进入实时/准实时工作事件同步。"
        if sync_action == "master_data_index":
            return "只同步业务对象索引和关键字段摘要。"
        if sync_action == "document_index_only":
            return "只登记文档索引，不拉全文。"
        if sync_action == "knowledge_vectorize":
            return "同步全文到本地知识索引，并进入向量化队列。"
    if status == "policy_excluded":
        return "如需自动同步，请把该资源类型加入公司同步策略。"
    if status == "access_blocked":
        if identifier_issue and identifier_issue["code"] == "requires_user_authorization":
            return "请先完成飞书用户授权；系统会继续自动发现可同步资源。"
        if access_status == "bot_not_in_chat":
            return "请先在飞书中把机器人加入对应群，或停用该资源。"
        if access_status == "requires_user_authorization":
            return "请先完成飞书用户授权；确认后系统会继续自动发现并同步。"
        return "请先处理飞书成员关系或用户授权；确认后系统会继续自动发现并同步。"
    if status == "manual_review_required":
        if identifier_issue:
            return "等待系统自动发现该类资源；如长期未发现，请查看权限和事件订阅状态。"
        return "需要先确认该资源的业务价值、权限和同步方式。"
    if status == "skipped":
        return "当前策略要求跳过该资源。"
    return reason


def _has_redacted_identifier(resource: Any) -> bool:
    values = [
        getattr(resource, "resource_id", None),
        getattr(resource, "resource_sub_id", None),
    ]
    return any(any(marker in str(value) for marker in REDACTION_MARKERS) for value in values if value)


def _resource_access_status(resource: Any) -> str | None:
    config = getattr(resource, "config_json", None) or {}
    if not isinstance(config, dict):
        return None
    governance = config.get("governance") or {}
    if not isinstance(governance, dict):
        return None
    status = governance.get("status")
    return str(status) if status else None


def _resource_identifier_issue(resource: Any, normalized_type: str) -> dict[str, str] | None:
    resource_id = str(getattr(resource, "resource_id", "") or "").strip()
    resource_sub_id = str(getattr(resource, "resource_sub_id", "") or "").strip()
    config = getattr(resource, "config_json", None) or {}
    if not isinstance(config, dict):
        config = {}

    if normalized_type == "chat" and not resource_id:
        return _missing_identifier("auto_discovered_chat")
    if normalized_type == "approval" and not resource_id:
        return _missing_identifier("approval_resource_id")
    if normalized_type == "mailbox" and (not resource_id or not (resource_sub_id or config.get("folder_id"))):
        return _missing_identifier("auto_discovered_mailbox")
    if normalized_type == "bitable" and (not resource_id or not resource_sub_id):
        return _missing_identifier("auto_discovered_bitable")
    if normalized_type == "doc" and not resource_id:
        return _missing_identifier("auto_discovered_document")
    if normalized_type == "wiki" and not resource_id:
        return _missing_identifier("auto_discovered_wiki")
    if normalized_type == "calendar" and resource_id.lower() == "feishu:calendar":
        return {
            "code": "requires_user_authorization",
            "reason": "日历能力需要用户授权或自动发现到具体日历资源；当前通用能力标识只能进入监控。",
        }
    return None


def _missing_identifier(name: str) -> dict[str, str]:
    return {
        "code": "missing_required_identifier",
        "reason": f"缺少必需的飞书资源标识：{name}。",
    }
