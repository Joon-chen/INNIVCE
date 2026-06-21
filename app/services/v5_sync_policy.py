from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.serialization import json_safe
from app.models.entities import Company, CompanySetting
from app.services.v5_sync_strategy import DEFAULT_REALTIME_RESOURCE_TYPES

POLICY_KEY = "v5_resource_sync_policy"


def default_v5_resource_sync_policy() -> dict[str, Any]:
    return {
        "enabled": settings.auto_v5_resource_sync_enabled,
        "interval_seconds": settings.auto_v5_resource_sync_interval_seconds,
        "limit_resources": settings.auto_v5_resource_sync_limit_resources,
        "event_limit": settings.auto_v5_resource_sync_event_limit,
        "max_pages": settings.auto_v5_resource_sync_max_pages,
        "resource_types": _parse_csv(settings.auto_v5_resource_sync_resource_types) or DEFAULT_REALTIME_RESOURCE_TYPES,
        "statuses": _parse_csv(settings.auto_v5_resource_sync_statuses),
        "extract_items": True,
        "large_document_mode": "index_only",
        "bitable_mode": "master_data_index",
        "memory_mode": "stable_facts_only",
        "vector_mode": "summaries_and_hot_knowledge",
        "source": "settings_default",
    }


def get_v5_resource_sync_policy(db: Session, company_id: UUID) -> dict[str, Any]:
    setting = _company_setting(db, company_id)
    stored = (setting.settings or {}).get(POLICY_KEY) if setting else None
    return normalize_v5_resource_sync_policy(stored)


def update_v5_resource_sync_policy(db: Session, company_id: UUID, data: dict[str, Any]) -> dict[str, Any]:
    setting = _ensure_company_setting(db, company_id)
    current = normalize_v5_resource_sync_policy((setting.settings or {}).get(POLICY_KEY))
    updated = normalize_v5_resource_sync_policy({**current, **data, "source": "console"})
    setting.settings = json_safe({**(setting.settings or {}), POLICY_KEY: updated})
    db.commit()
    return updated


def get_v5_resource_sync_policy_payload(db: Session, *, company_id: UUID) -> dict[str, Any]:
    return {"company_id": str(company_id), "policy": get_v5_resource_sync_policy(db, company_id)}


def update_v5_resource_sync_policy_payload(
    db: Session,
    *,
    company_id: UUID,
    data: dict[str, Any],
) -> dict[str, Any]:
    policy = update_v5_resource_sync_policy(db, company_id, data)
    return {"company_id": str(company_id), "policy": policy}


def enabled_v5_resource_sync_policies(db: Session) -> list[tuple[UUID, dict[str, Any]]]:
    policies: list[tuple[UUID, dict[str, Any]]] = []
    configured_company_ids: set[UUID] = set()
    settings_rows = db.scalars(select(CompanySetting)).all()
    for setting in settings_rows:
        stored_policy = (setting.settings or {}).get(POLICY_KEY)
        if stored_policy is None:
            continue
        configured_company_ids.add(setting.company_id)
        policy = normalize_v5_resource_sync_policy(stored_policy)
        if policy["enabled"]:
            policies.append((setting.company_id, policy))

    fallback = default_v5_resource_sync_policy()
    if fallback["enabled"]:
        company_ids = db.scalars(select(Company.id).where(Company.status == "active")).all()
        policies.extend((company_id, fallback) for company_id in company_ids if company_id not in configured_company_ids)
    return policies


def normalize_v5_resource_sync_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_v5_resource_sync_policy()
    data = {**defaults, **(raw or {})}
    return {
        "enabled": bool(data.get("enabled")),
        "interval_seconds": _clamp_int(data.get("interval_seconds"), default=900, minimum=60, maximum=86400),
        "limit_resources": _clamp_int(data.get("limit_resources"), default=10, minimum=1, maximum=100),
        "event_limit": _clamp_int(data.get("event_limit"), default=20, minimum=1, maximum=200),
        "max_pages": _clamp_int(data.get("max_pages"), default=2, minimum=1, maximum=20),
        "resource_types": _normalize_list(data.get("resource_types")),
        "statuses": _normalize_list(data.get("statuses")),
        "extract_items": bool(data.get("extract_items", True)),
        "large_document_mode": str(data.get("large_document_mode") or "index_only"),
        "bitable_mode": str(data.get("bitable_mode") or "master_data_index"),
        "memory_mode": str(data.get("memory_mode") or "stable_facts_only"),
        "vector_mode": str(data.get("vector_mode") or "summaries_and_hot_knowledge"),
        "source": str(data.get("source") or "settings_default"),
    }


def _company_setting(db: Session, company_id: UUID) -> CompanySetting | None:
    return db.scalar(select(CompanySetting).where(CompanySetting.company_id == company_id))


def _ensure_company_setting(db: Session, company_id: UUID) -> CompanySetting:
    setting = _company_setting(db, company_id)
    if setting:
        return setting
    company = db.get(Company, company_id)
    setting = CompanySetting(
        company_id=company_id,
        status=getattr(company, "status", "active") if company else "active",
        settings={"source": "v5_sync_policy"},
    )
    db.add(setting)
    db.flush()
    return setting


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = _parse_csv(value)
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    result: list[str] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def _parse_csv(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)
