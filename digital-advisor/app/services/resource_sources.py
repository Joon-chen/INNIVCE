from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.serialization import json_safe
from app.models.entities import Account, Resource, ResourceSource
from app.services.data.resource_sources import (
    EXTERNAL_MAIL_ACCOUNT,
    FEISHU_USER_IDENTITY,
    LOCAL_IMPORT,
    normalize_source_type,
    source_policy_payload,
    source_priority,
)


def upsert_resource_source(
    db: Session,
    *,
    resource: Resource,
    source_type: str,
    source_account_id: str | UUID | None = "",
    source_account_label: str | None = None,
    access_level: str = "read",
    can_sync: bool = True,
    priority: int | None = None,
    visibility_scope: str | None = None,
    allowed_user_ids: list[str] | None = None,
    allowed_roles: list[str] | None = None,
    allowed_departments: list[str] | None = None,
    limitations: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> ResourceSource:
    clean_source_type = normalize_source_type(source_type)
    clean_account_id = str(source_account_id or "").strip()
    source = db.scalar(
        select(ResourceSource)
        .where(ResourceSource.resource_id == resource.id)
        .where(ResourceSource.source_type == clean_source_type)
        .where(ResourceSource.source_account_id == clean_account_id)
    )
    if source is None:
        source = ResourceSource(
            company_id=resource.company_id,
            resource_id=resource.id,
            source_type=clean_source_type,
            source_account_id=clean_account_id,
        )
        source.resource = resource
        db.add(source)
    source.company_id = resource.company_id
    source.source_account_label = source_account_label or source.source_account_label
    source.access_level = access_level
    source.can_sync = can_sync
    source.priority = priority if priority is not None else source_priority(clean_source_type)
    source.visibility_scope = visibility_scope or _default_visibility_scope(clean_source_type, resource)
    source.allowed_user_ids = json_safe(allowed_user_ids or [])
    source.allowed_roles = json_safe(allowed_roles or [])
    source.allowed_departments = json_safe(allowed_departments or [])
    source.limitations = json_safe(limitations or {})
    source.settings = json_safe(settings or {})
    source.last_seen_at = datetime.now(UTC)
    return source


def preferred_sync_source(db: Session, resource: Resource) -> ResourceSource | None:
    return db.scalar(
        select(ResourceSource)
        .where(ResourceSource.resource_id == resource.id)
        .where(ResourceSource.can_sync.is_(True))
        .order_by(ResourceSource.priority.desc(), ResourceSource.last_seen_at.desc())
    )


def resource_source_payloads(db: Session, resource_ids: list[UUID]) -> dict[UUID, list[dict[str, Any]]]:
    if not resource_ids:
        return {}
    rows = db.scalars(
        select(ResourceSource)
        .where(ResourceSource.resource_id.in_(resource_ids))
        .order_by(ResourceSource.priority.desc(), ResourceSource.last_seen_at.desc())
    ).all()
    result: dict[UUID, list[dict[str, Any]]] = {}
    for source in rows:
        result.setdefault(source.resource_id, []).append(resource_source_payload(source))
    return result


def resource_source_payload(source: ResourceSource | None) -> dict[str, Any] | None:
    if source is None:
        return None
    policy = source_policy_payload(source)
    settings = getattr(source, "settings", None) or {}
    return {
        "id": str(source.id),
        "source_type": source.source_type,
        "normalized_source_type": policy["normalized_source_type"],
        "data_source": policy["data_source"],
        "identity_type": policy["identity_type"],
        "account_type": settings.get("account_type"),
        "source_account_id": source.source_account_id,
        "source_account_label": source.source_account_label,
        "access_level": source.access_level,
        "can_sync": source.can_sync,
        "priority": source.priority,
        "visibility_scope": source.visibility_scope,
        "allowed_user_ids": source.allowed_user_ids,
        "allowed_roles": source.allowed_roles,
        "allowed_departments": source.allowed_departments,
        "limitations": source.limitations,
        "last_seen_at": source.last_seen_at.isoformat() if source.last_seen_at else None,
    }


def upsert_mail_account_resource(db: Session, account: Account, *, folder_id: str = "INBOX") -> Resource:
    mailbox_id = account.email_address or account.external_account_id or str(account.id)
    clean_folder_id = (folder_id or "INBOX").strip() or "INBOX"
    resource = db.scalar(
        select(Resource)
        .where(Resource.company_id == account.company_id)
        .where(Resource.platform == "mail")
        .where(Resource.resource_type == "mail_folder")
        .where(Resource.resource_id == mailbox_id)
        .where(Resource.resource_sub_id == clean_folder_id)
    )
    visibility_scope = _account_visibility_scope(account)
    if resource is None:
        resource = Resource(
            id=uuid4(),
            company_id=account.company_id,
            platform="mail",
            resource_type="mail_folder",
            resource_id=mailbox_id,
            resource_sub_id=clean_folder_id,
        )
        db.add(resource)
    resource.resource_name = f"{account.display_name} / {clean_folder_id}"
    resource.sync_mode = (account.settings or {}).get("sync_mode") or "scheduled"
    resource.permission_level = visibility_scope
    resource.data_classification = "personal"
    resource.business_domain = "communications"
    resource.enabled = account.is_active
    resource.config_json = json_safe(
        {
            **(resource.config_json or {}),
            "account_id": str(account.id),
            "provider": account.provider,
            "account_type": getattr(account, "account_type", None),
            "email_address": account.email_address,
            "folder_id": clean_folder_id,
            "source": "manual_mail_account",
        }
    )
    upsert_resource_source(
        db,
        resource=resource,
        source_type=EXTERNAL_MAIL_ACCOUNT,
        source_account_id=account.id,
        source_account_label=account.display_name,
        access_level="read",
        can_sync=account.is_active,
        priority=source_priority(EXTERNAL_MAIL_ACCOUNT),
        visibility_scope=visibility_scope,
        allowed_user_ids=(account.settings or {}).get("allowed_user_ids") or [],
        allowed_roles=(account.settings or {}).get("allowed_roles") or [],
        allowed_departments=(account.settings or {}).get("allowed_departments") or [],
        settings={
            "provider": account.provider,
            "account_type": getattr(account, "account_type", None),
            "email_address": account.email_address,
            "folder_id": clean_folder_id,
            "manual_entry": True,
        },
    )
    return resource


def _default_visibility_scope(source_type: str, resource: Resource) -> str:
    normalized = normalize_source_type(source_type)
    if normalized == FEISHU_USER_IDENTITY:
        return "user"
    if normalized == EXTERNAL_MAIL_ACCOUNT:
        return "private" if resource.permission_level == "private" else resource.permission_level
    if normalized == LOCAL_IMPORT:
        return "owner"
    return resource.permission_level or "company"


def _account_visibility_scope(account: Account) -> str:
    settings = account.settings or {}
    configured = settings.get("personal_visibility_scope") or settings.get("visibility_scope")
    return str(configured or "owner")
