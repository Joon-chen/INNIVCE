from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Resource
from app.services.sync_runs import finish_sync_run, start_sync_run
from app.services.v5_resource_status import classify_resource_sync_status
from app.services.v5_resources import resource_to_v5_payload
from app.services.v5_sync_decisions import sync_decision_for_resource
from app.services.v5_workspace import sync_v5_resource


SyncResourceCallable = Callable[..., Awaitable[dict[str, Any]]]


def select_company_auto_sync_resources(
    db: Session,
    *,
    company_id: UUID,
    policy: dict[str, Any],
    resource_type: str | None = None,
    statuses: list[str] | None = None,
    limit_resources: int | None = None,
) -> list[Resource]:
    allowed_types = set(policy.get("resource_types") or [])
    allowed_statuses = set(statuses if statuses is not None else policy.get("statuses") or [])
    resource_limit = int(limit_resources or policy.get("limit_resources") or 10)
    query = (
        select(Resource)
        .where(Resource.company_id == company_id)
        .where(Resource.enabled.is_(True))
        .order_by(Resource.last_sync_at.asc().nullsfirst(), Resource.created_at.asc())
    )
    resources: list[Resource] = []
    for resource in db.scalars(query).all():
        payload = resource_to_v5_payload(resource)
        normalized_type = payload["resource_type"]
        if resource_type and normalized_type != resource_type and resource.resource_type != resource_type:
            continue
        decision = sync_decision_for_resource(resource, policy)
        if not decision["auto_sync_allowed"]:
            continue
        if allowed_types and normalized_type not in allowed_types and resource.resource_type not in allowed_types:
            continue
        status = classify_resource_sync_status(resource, policy=policy)
        if allowed_statuses and status not in allowed_statuses:
            continue
        resources.append(resource)
        if len(resources) >= resource_limit:
            break
    return resources


def company_auto_sync_preview(
    db: Session,
    *,
    company_id: UUID,
    policy: dict[str, Any],
    resource_type: str | None = None,
    statuses: list[str] | None = None,
    limit_resources: int | None = None,
) -> dict[str, Any]:
    resources = select_company_auto_sync_resources(
        db,
        company_id=company_id,
        policy=policy,
        resource_type=resource_type,
        statuses=statuses,
        limit_resources=limit_resources,
    )
    items = [_preview_item(resource, policy=policy) for resource in resources]
    return {
        "company_id": str(company_id),
        "count": len(items),
        "selected_resource_ids": [item["resource_id"] for item in items],
        "sync_actions": _count_by(items, "sync_action"),
        "decision_statuses": _count_by(items, "decision_status"),
        "items": items,
    }


def preview_company_resource_batch_sync(
    db: Session,
    *,
    company_id: UUID,
    resource_type: str | None = None,
    statuses: list[str] | None = None,
    limit_resources: int | None = None,
    large_document_mode: str = "index_only",
    bitable_mode: str = "master_data_index",
    memory_mode: str = "stable_facts_only",
    vector_mode: str = "summaries_and_hot_knowledge",
) -> dict[str, Any]:
    policy = _request_batch_sync_policy(
        large_document_mode=large_document_mode,
        bitable_mode=bitable_mode,
        memory_mode=memory_mode,
        vector_mode=vector_mode,
    )
    preview = company_auto_sync_preview(
        db,
        company_id=company_id,
        policy=policy,
        resource_type=resource_type,
        statuses=statuses,
        limit_resources=limit_resources,
    )
    return {**preview, "statuses": statuses, "resource_type": resource_type}


async def sync_company_resource_batch(
    db: Session,
    *,
    company_id: UUID,
    resource_type: str | None = None,
    statuses: list[str] | None = None,
    limit_resources: int | None = None,
    limit: int = 20,
    max_pages: int = 3,
    extract_items: bool = False,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    large_document_mode: str = "index_only",
    bitable_mode: str = "master_data_index",
    memory_mode: str = "stable_facts_only",
    vector_mode: str = "summaries_and_hot_knowledge",
) -> dict[str, Any]:
    policy = _request_batch_sync_policy(
        large_document_mode=large_document_mode,
        bitable_mode=bitable_mode,
        memory_mode=memory_mode,
        vector_mode=vector_mode,
    )
    resources = select_company_auto_sync_resources(
        db,
        company_id=company_id,
        policy=policy,
        resource_type=resource_type,
        statuses=statuses,
        limit_resources=limit_resources,
    )
    results = []
    for resource in resources:
        results.append(
            await sync_v5_resource(
                db,
                resource_id=resource.id,
                limit=limit,
                max_pages=max_pages,
                extract_items=extract_items,
                start_time=start_time,
                end_time=end_time,
                large_document_mode=large_document_mode,
                bitable_mode=bitable_mode,
                memory_mode=memory_mode,
                vector_mode=vector_mode,
            )
        )
    return {
        "count": len(results),
        "selected_resource_ids": [str(resource.id) for resource in resources],
        "statuses": statuses,
        "resource_type": resource_type,
        "saved_count": sum(item.get("saved_count", 0) for item in results),
        "items": results,
    }


async def sync_company_auto_resources(
    db: Session,
    *,
    company_id: UUID,
    policy: dict[str, Any],
    sync_resource: SyncResourceCallable = sync_v5_resource,
) -> dict[str, Any]:
    sync_run = start_sync_run(
        db,
        company_id=company_id,
        provider="v5_resource",
        sync_type="auto",
        cursor={"policy": policy, "orchestrator": "v5_auto_sync"},
    )
    db.commit()
    resources = select_company_auto_sync_resources(db, company_id=company_id, policy=policy)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for resource in resources:
        try:
            result = await sync_resource(
                db,
                resource_id=resource.id,
                limit=policy["event_limit"],
                max_pages=policy["max_pages"],
                extract_items=policy["extract_items"],
                large_document_mode=policy["large_document_mode"],
                bitable_mode=policy["bitable_mode"],
                memory_mode=policy["memory_mode"],
                vector_mode=policy["vector_mode"],
            )
            results.append(result)
        except Exception as exc:
            db.rollback()
            errors.append({"resource_id": str(resource.id), "error": str(exc)[:500]})
    finish_sync_run(
        sync_run,
        status="success" if not errors else "partial",
        saved_count=sum(item.get("saved_count", 0) for item in results),
        error_count=len(errors),
        summary={
            "resource_ids": [str(item.id) for item in resources],
            "result_count": len(results),
            "error_count": len(errors),
        },
    )
    db.commit()
    return {
        "company_id": str(company_id),
        "sync_run_id": str(sync_run.id),
        "count": len(results),
        "selected_resource_ids": [str(item.id) for item in resources],
        "saved_count": sum(item.get("saved_count", 0) for item in results),
        "items": results,
        "errors": errors,
    }


def company_auto_sync_due(db: Session, *, company_id: UUID, policy: dict[str, Any]) -> bool:
    from app.models.entities import SyncRun

    latest = db.scalar(
        select(SyncRun)
        .where(SyncRun.company_id == company_id)
        .where(SyncRun.provider == "v5_resource")
        .where(SyncRun.sync_type == "auto")
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    )
    if not latest or not latest.started_at:
        return True
    started_at = latest.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    elapsed_seconds = (datetime.now(UTC) - started_at).total_seconds()
    return elapsed_seconds >= float(policy["interval_seconds"])


def _request_batch_sync_policy(
    *,
    large_document_mode: str,
    bitable_mode: str,
    memory_mode: str,
    vector_mode: str,
) -> dict[str, Any]:
    return {
        "large_document_mode": large_document_mode,
        "bitable_mode": bitable_mode,
        "memory_mode": memory_mode,
        "vector_mode": vector_mode,
    }


def _preview_item(resource: Resource, *, policy: dict[str, Any]) -> dict[str, Any]:
    decision = sync_decision_for_resource(resource, policy)
    payload = resource_to_v5_payload(resource)
    return {
        "resource_id": str(resource.id),
        "resource_name": resource.resource_name,
        "resource_type": payload["resource_type"],
        "legacy_resource_type": resource.resource_type,
        "data_layer": decision["data_layer"],
        "sync_action": decision["sync_action"],
        "decision_status": decision["status"],
        "executable": decision["executable"],
        "next_action": decision["next_action"],
        "reason": decision["reason"],
    }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts
