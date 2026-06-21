from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.core.serialization import json_safe
from app.models.entities import FeishuAppConfig, Resource
from app.services.resource_registry import feishu_discovered_resource_spec, upsert_feishu_discovered_resource, upsert_resource
from app.services.v5_resources import resource_to_v5_payload


class FeishuResourceRegistrationRequest(BaseModel):
    company_id: UUID
    app_config_id: UUID | None = None
    resource_type: str
    external_id: str
    name: str | None = None
    sync_enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)


def register_v5_feishu_resource_from_request(
    db: Session,
    data: FeishuResourceRegistrationRequest,
) -> dict[str, Any]:
    return register_v5_feishu_resource_payload(
        db,
        company_id=data.company_id,
        app_config_id=data.app_config_id,
        resource_type=data.resource_type,
        external_id=data.external_id,
        name=data.name,
        sync_enabled=data.sync_enabled,
        settings=data.settings,
    )


def dashboard_resource_summary(db: Session) -> dict[str, Any]:
    legacy_retirement = legacy_feishu_resource_retirement_status(db)
    return {
        "enabled_resources": enabled_v5_feishu_resource_count(db),
        "resource_counts": v5_feishu_resource_counts(db),
        "migration": {
            "resource_model": "Resource",
            "legacy_feishu_resources": legacy_retirement["enabled_legacy_resources"],
            "legacy_retirement": legacy_retirement,
        },
    }


def v5_feishu_resource_counts(db: Session) -> dict[str, int]:
    return {
        str(resource_type): int(count)
        for resource_type, count in db.execute(
            select(Resource.resource_type, func.count())
            .where(Resource.platform == "feishu")
            .where(Resource.enabled.is_(True))
            .group_by(Resource.resource_type)
            .order_by(func.count().desc())
        ).all()
    }


def enabled_v5_feishu_resource_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Resource)
            .where(Resource.platform == "feishu")
            .where(Resource.enabled.is_(True))
        )
        or 0
    )


def enabled_legacy_feishu_resource_count(db: Session) -> int:
    return _legacy_count_or_zero(db, "select count(*) from feishu_resources where sync_enabled is true")


def legacy_feishu_resource_count(db: Session) -> int:
    return _legacy_count_or_zero(db, "select count(*) from feishu_resources")


def mapped_legacy_feishu_resource_count(db: Session) -> int:
    return (
        db.scalar(select(func.count()).select_from(Resource).where(Resource.legacy_feishu_resource_id.is_not(None)))
        or 0
    )


def legacy_feishu_resource_retirement_status(db: Session) -> dict[str, Any]:
    total_legacy = legacy_feishu_resource_count(db)
    enabled_legacy = enabled_legacy_feishu_resource_count(db)
    mapped_to_v5 = mapped_legacy_feishu_resource_count(db)
    unmapped_legacy = max(total_legacy - mapped_to_v5, 0)
    can_drop_table = total_legacy == 0 or unmapped_legacy == 0
    table_status = "active" if total_legacy or enabled_legacy else "empty_or_retired"
    return {
        "legacy_table_status": table_status,
        "total_legacy_resources": total_legacy,
        "enabled_legacy_resources": enabled_legacy,
        "mapped_to_v5_resources": mapped_to_v5,
        "unmapped_legacy_resources": unmapped_legacy,
        "can_drop_legacy_table": can_drop_table,
        "next_action": legacy_retirement_next_action(can_drop_table=can_drop_table, table_status=table_status),
    }


def legacy_retirement_next_action(*, can_drop_table: bool, table_status: str) -> str:
    if table_status == "empty_or_retired":
        return "keep_legacy_table_retired"
    return "drop_legacy_table" if can_drop_table else "run_legacy_migration_fallback"


def register_v5_feishu_resource(
    db: Session,
    *,
    company_id: UUID,
    app_config_id: UUID | None,
    resource_type: str,
    external_id: str,
    name: str | None,
    sync_enabled: bool,
    settings: dict[str, Any],
    app_config: FeishuAppConfig | None = None,
) -> Resource:
    item = {
        "resource_type": resource_type,
        "external_id": external_id,
        "name": name,
        "sync_enabled": sync_enabled,
        "settings": settings,
    }
    if app_config:
        return upsert_feishu_discovered_resource(
            db,
            app_config=app_config,
            item=item,
            source_label="operations_feishu_resource_upsert",
        )
    spec = feishu_discovered_resource_spec(item)
    return upsert_resource(
        db,
        company_id=company_id,
        platform="feishu",
        resource_type=spec["resource_type"],
        resource_id=spec["resource_id"],
        resource_sub_id=spec.get("resource_sub_id"),
        resource_name=spec["resource_name"],
        sync_mode=spec["sync_mode"],
        permission_level=spec["permission_level"],
        data_classification=spec["data_classification"],
        business_domain=spec["business_domain"],
        enabled=sync_enabled,
        config_json={
            "source": "operations_feishu_resource_upsert",
            "external_id": external_id,
            "settings": json_safe(settings),
        },
        app_config_id=app_config_id,
    )


def register_v5_feishu_resource_payload(
    db: Session,
    *,
    company_id: UUID,
    app_config_id: UUID | None,
    resource_type: str,
    external_id: str,
    name: str | None,
    sync_enabled: bool,
    settings: dict[str, Any],
) -> dict[str, Any]:
    app_config = db.get(FeishuAppConfig, app_config_id) if app_config_id else None
    if app_config and app_config.company_id != company_id:
        raise HTTPException(status_code=400, detail="Feishu app_config_id belongs to another company")
    v5_resource = register_v5_feishu_resource(
        db,
        company_id=company_id,
        app_config_id=app_config_id,
        resource_type=resource_type,
        external_id=external_id,
        name=name,
        sync_enabled=sync_enabled,
        settings=settings,
        app_config=app_config,
    )
    db.commit()
    return {
        "id": str(v5_resource.id),
        "v5_resource_id": str(v5_resource.id),
        "source_model": "Resource",
        "resource_type": v5_resource.resource_type,
        "resource_id": v5_resource.resource_id,
        "resource_sub_id": v5_resource.resource_sub_id,
        "resource_name": v5_resource.resource_name,
        "external_id": external_id,
        "legacy_id": None,
    }


def list_v5_feishu_resources(db: Session, *, company_id: UUID | None = None) -> dict[str, Any]:
    query = select(Resource).where(Resource.platform == "feishu").order_by(Resource.created_at.desc())
    if company_id:
        query = query.where(Resource.company_id == company_id)
    resources = list(db.scalars(query).all())
    items = [operations_feishu_resource_payload(item) for item in resources]
    return {"items": items, "counts": count_by(items, "resource_type"), "source_model": "Resource"}


def operations_feishu_resource_payload(resource: Resource) -> dict[str, Any]:
    payload = resource_to_v5_payload(resource)
    settings = payload.get("config_json") or {}
    payload.update(
        {
            "source_model": "Resource",
            "v5_resource_id": payload["id"],
            "external_id": settings.get("external_id") or resource.resource_id,
            "name": payload.get("resource_name"),
            "sync_enabled": payload.get("enabled"),
            "health": v5_resource_health(resource),
            "settings": settings.get("settings") or {},
            "legacy_id": payload.get("legacy_resource_id"),
            "legacy_resource_type": payload.get("legacy_resource_type"),
            "legacy_external_id": settings.get("external_id"),
            "legacy_name": None,
            "legacy_sync_enabled": None,
            "legacy_settings": None,
        }
    )
    return payload


def v5_resource_health(item: Resource) -> str:
    if item.resource_type == "chat" and not item.resource_id.startswith("oc_"):
        return "suspect"
    return "ok"


def count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _legacy_count_or_zero(db: Session, query: str) -> int:
    try:
        return db.scalar(text(query)) or 0
    except (OperationalError, ProgrammingError):
        rollback = getattr(db, "rollback", None)
        if callable(rollback):
            rollback()
        return 0
