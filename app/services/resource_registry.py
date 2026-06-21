from dataclasses import dataclass
from typing import Any
from uuid import UUID
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.core.serialization import json_safe
from app.models.entities import FeishuAppConfig, Resource, ResourcePermission, Role
from app.services.data.resource_sources import FEISHU_APP_IDENTITY, FEISHU_USER_IDENTITY, LOCAL_IMPORT
from app.services.resource_sources import upsert_resource_source


@dataclass(frozen=True)
class LegacyFeishuResourceMigration:
    legacy_id: UUID
    resource_type: str
    external_id: str
    name: str | None
    settings: dict[str, Any]
    resource: Resource


def upsert_resource(
    db: Session,
    *,
    company_id: UUID,
    platform: str,
    resource_type: str,
    resource_id: str,
    resource_name: str | None = None,
    resource_sub_id: str | None = None,
    sync_mode: str = "manual",
    permission_level: str = "company",
    data_classification: str = "company",
    business_domain: str = "general",
    enabled: bool = True,
    config_json: dict[str, Any] | None = None,
    app_config_id: UUID | None = None,
    legacy_feishu_resource_id: UUID | None = None,
) -> Resource:
    query = (
        select(Resource)
        .where(Resource.company_id == company_id)
        .where(Resource.platform == platform)
        .where(Resource.resource_type == resource_type)
        .where(Resource.resource_id == resource_id)
    )
    if resource_sub_id is None:
        query = query.where(Resource.resource_sub_id.is_(None))
    else:
        query = query.where(Resource.resource_sub_id == resource_sub_id)
    resource = db.scalar(query)
    if resource is None:
        resource = Resource(
            id=uuid4(),
            company_id=company_id,
            platform=platform,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_sub_id=resource_sub_id,
        )
        db.add(resource)
    resource.resource_name = resource_name or resource.resource_name or resource_id
    resource.sync_mode = sync_mode
    resource.permission_level = permission_level
    resource.data_classification = data_classification
    resource.business_domain = business_domain
    resource.enabled = enabled
    resource.config_json = json_safe(config_json or {})
    resource.app_config_id = app_config_id
    resource.legacy_feishu_resource_id = legacy_feishu_resource_id
    return resource


def upsert_resource_permission(
    db: Session,
    *,
    company_id: UUID,
    resource: Resource,
    role_name: str,
    permission_level: str = "admin",
    conditions_json: dict[str, Any] | None = None,
) -> ResourcePermission | None:
    role = db.scalar(select(Role).where(Role.company_id == company_id).where(Role.name == role_name))
    if role is None:
        return None
    permission = db.scalar(
        select(ResourcePermission)
        .where(ResourcePermission.company_id == company_id)
        .where(ResourcePermission.resource_id == resource.id)
        .where(ResourcePermission.role_id == role.id)
    )
    if permission is None:
        permission = ResourcePermission(company_id=company_id, resource_id=resource.id, role_id=role.id)
        db.add(permission)
    permission.permission_level = permission_level
    permission.conditions_json = json_safe(conditions_json or {})
    permission.enabled = True
    return permission


def upsert_feishu_discovered_resource(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    item: dict[str, Any],
    legacy_feishu_resource_id: UUID | None = None,
    source_label: str = "feishu_resource_discovery",
) -> Resource:
    spec = feishu_discovered_resource_spec(item)
    resource = upsert_resource(
        db,
        company_id=app_config.company_id,
        platform="feishu",
        resource_type=spec["resource_type"],
        resource_name=spec["resource_name"],
        resource_id=spec["resource_id"],
        resource_sub_id=spec.get("resource_sub_id"),
        sync_mode=spec["sync_mode"],
        permission_level=spec["permission_level"],
        data_classification=spec["data_classification"],
        business_domain=spec["business_domain"],
        enabled=bool(item.get("sync_enabled", True)),
        config_json={
            "source": source_label,
            "external_id": item.get("external_id"),
            "settings": json_safe(item.get("settings") or {}),
        },
        legacy_feishu_resource_id=legacy_feishu_resource_id,
        app_config_id=app_config.id,
    )
    upsert_resource_source(
        db,
        resource=resource,
        source_type=_source_type_for_feishu_item(item),
        source_account_id=_source_account_id_for_feishu_item(item, app_config=app_config),
        source_account_label=_source_account_label_for_feishu_item(item, app_config=app_config),
        access_level="admin" if resource.permission_level == "company" else "read",
        can_sync=bool(item.get("sync_enabled", True)),
        visibility_scope=resource.permission_level,
        settings={
            "source": source_label,
            "item_source": (item.get("settings") or {}).get("source"),
            "external_id": item.get("external_id"),
        },
    )
    upsert_resource_permission(
        db,
        company_id=app_config.company_id,
        resource=resource,
        role_name="owner",
        permission_level="admin",
        conditions_json={"source": source_label},
    )
    return resource


def migrate_legacy_feishu_resources(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    resource_type: str,
    limit: int | None = None,
) -> list[LegacyFeishuResourceMigration]:
    if not _legacy_feishu_resources_table_exists(db):
        return []
    query = """
        select id, resource_type, external_id, name, sync_enabled, settings
        from feishu_resources
        where company_id = :company_id
          and resource_type = :resource_type
          and sync_enabled is true
        order by created_at desc
    """
    params: dict[str, Any] = {"company_id": app_config.company_id, "resource_type": resource_type}
    try:
        if limit is not None:
            query = f"{query}\nlimit :limit"
            params["limit"] = limit
        legacy_resources = db.execute(text(query), params).mappings().all()
    except (OperationalError, ProgrammingError):
        return []
    migrations: list[LegacyFeishuResourceMigration] = []
    for legacy in legacy_resources:
        external_id = str(legacy["external_id"] or "").strip()
        if not external_id:
            continue
        settings = json_safe({**(legacy["settings"] or {}), "source": "legacy_feishu_resources"})
        registered_resource = upsert_feishu_discovered_resource(
            db,
            app_config=app_config,
            item={
                "resource_type": legacy["resource_type"],
                "external_id": external_id,
                "name": legacy["name"] or external_id,
                "sync_enabled": True,
                "settings": settings,
            },
            legacy_feishu_resource_id=legacy["id"],
            source_label="legacy_feishu_resources",
        )
        migrations.append(
            LegacyFeishuResourceMigration(
                legacy_id=legacy["id"],
                resource_type=legacy["resource_type"],
                external_id=external_id,
                name=legacy["name"],
                settings=settings,
                resource=registered_resource,
            )
        )
    return migrations


def _legacy_feishu_resources_table_exists(db: Session) -> bool:
    try:
        return db.execute(text("select to_regclass('public.feishu_resources')"), {}).scalar_one_or_none() is not None
    except (OperationalError, ProgrammingError):
        return False


def _source_type_for_feishu_item(item: dict[str, Any]) -> str:
    settings = item.get("settings") or {}
    source = str(settings.get("source") or "")
    if source == "feishu_user_oauth":
        return FEISHU_USER_IDENTITY
    if source == "manual_import":
        return LOCAL_IMPORT
    return FEISHU_APP_IDENTITY


def _source_account_id_for_feishu_item(item: dict[str, Any], *, app_config: FeishuAppConfig) -> UUID | str:
    settings = item.get("settings") or {}
    if str(settings.get("source") or "") == "feishu_user_oauth":
        return str(settings.get("account_id") or app_config.id)
    return app_config.id


def _source_account_label_for_feishu_item(item: dict[str, Any], *, app_config: FeishuAppConfig) -> str:
    settings = item.get("settings") or {}
    if str(settings.get("source") or "") == "feishu_user_oauth":
        return str(settings.get("account_label") or "个人飞书账号")
    return app_config.name or app_config.app_id


def feishu_discovered_resource_spec(item: dict[str, Any]) -> dict[str, Any]:
    resource_type = str(item.get("resource_type") or "").strip()
    external_id = str(item.get("external_id") or "").strip()
    settings = item.get("settings") or {}
    name = item.get("name") or external_id

    resource_id = external_id
    resource_sub_id = None
    sync_mode = "manual"
    permission_level = "company"
    data_classification = "company"
    business_domain = "general"

    if resource_type == "chat":
        sync_mode = "realtime_and_history"
        permission_level = "team"
        business_domain = "communications"
    elif resource_type == "mail_folder":
        resource_id = str(settings.get("user_mailbox_id") or external_id.split(":", 1)[0])
        resource_sub_id = str(settings.get("folder_id") or external_id.split(":", 1)[-1])
        sync_mode = "scheduled"
        permission_level = "owner"
        data_classification = "personal"
        business_domain = "communications"
    elif resource_type == "approval_code":
        sync_mode = "scheduled"
        permission_level = "department"
        business_domain = "administration"
    elif resource_type == "bitable_table":
        resource_id = str(settings.get("app_token") or external_id.split(":", 1)[0])
        resource_sub_id = str(settings.get("table_id") or external_id.split(":", 1)[-1])
        sync_mode = "scheduled"
        permission_level = "department"
        business_domain = str(settings.get("business_domain") or "operations")
    elif resource_type == "drive_folder":
        sync_mode = "scheduled"
        permission_level = "department"
        business_domain = "knowledge"
    elif resource_type in {"drive_file", "bitable_app"}:
        sync_mode = "manual_or_scheduled"
        permission_level = "department"
        business_domain = "knowledge" if resource_type == "drive_file" else "operations"
    elif resource_type == "wiki_space":
        sync_mode = "manual_or_scheduled"
        permission_level = "department"
        business_domain = "knowledge"
    elif resource_type == "capability":
        sync_mode = str(settings.get("sync_mode") or "manual")
        permission_level = str(settings.get("permission_level") or "company")
        business_domain = _capability_business_domain(external_id)

    return {
        "resource_type": resource_type,
        "resource_name": name,
        "resource_id": resource_id,
        "resource_sub_id": resource_sub_id,
        "sync_mode": sync_mode,
        "permission_level": permission_level,
        "data_classification": data_classification,
        "business_domain": business_domain,
    }


def _capability_business_domain(external_id: str) -> str:
    if external_id == "feishu:contacts":
        return "administration"
    if external_id in {"feishu:calendar", "feishu:meetings"}:
        return "meeting"
    if external_id == "feishu:tasks":
        return "task"
    return "general"
