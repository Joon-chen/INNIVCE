from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import Company, Resource, ResourceSyncRun, WorkEvent
from app.services.resource_sources import resource_source_payloads
from app.services.v5_owner_actions import owner_actions_from_resource_items
from app.services.v5_resources import normalize_v5_resource_type, resource_to_v5_payload
from app.services.v5_sync_decisions import sync_decision_for_resource
from app.services.v5_sync_policy import get_v5_resource_sync_policy
from app.services.v5_workspace import _sync_args_for_resource


STALE_AFTER = timedelta(hours=24)


def list_resource_sync_status(db: Session, company_id: UUID | None = None) -> dict[str, Any]:
    resources = _resources(db, company_id=company_id)
    event_counts = _work_event_counts(db, company_id=company_id)
    latest_runs = _latest_sync_runs(db, company_id=company_id)
    latest_runs_by_resource = _latest_sync_runs_by_resource(db, company_id=company_id)
    policies = _policies_by_company(db, resources)
    sources_by_resource = resource_source_payloads(db, [item.id for item in resources])
    now = datetime.now(UTC)
    items = [
        _resource_sync_payload(
            item,
            now=now,
            event_counts=event_counts,
            policy=policies.get(item.company_id, {}),
            latest_run=latest_runs_by_resource.get(item.id),
            sources=sources_by_resource.get(item.id, []),
        )
        for item in resources
    ]
    items.sort(key=lambda item: (item["status_order"], item["resource_type"], item["resource_name"] or ""))
    return {
        "items": items,
        "counts": dict(Counter(item["status"] for item in items)),
        "governance_actions": owner_actions_from_resource_items(items),
        "latest_runs": latest_runs,
    }


def resource_monitoring_summary(db: Session, company_id: UUID | None = None) -> dict[str, Any]:
    resources = _resources(db, company_id=company_id)
    policies = _policies_by_company(db, resources)
    latest_runs_by_resource = _latest_sync_runs_by_resource(db, company_id=company_id)
    sources_by_resource = resource_source_payloads(db, [item.id for item in resources])
    now = datetime.now(UTC)
    sync_items = [
        _resource_sync_payload(
            item,
            now=now,
            event_counts={},
            policy=policies.get(item.company_id, {}),
            latest_run=latest_runs_by_resource.get(item.id),
            sources=sources_by_resource.get(item.id, []),
        )
        for item in resources
    ]
    resource_counts = Counter(item["resource_type"] for item in sync_items)
    platform_counts = Counter(item.platform for item in resources)
    action_counts = Counter(item["sync_action"] for item in sync_items)
    return {
        "counts": {
            "resources": len(resources),
            "enabled": sum(1 for item in resources if item.enabled),
            "disabled": sum(1 for item in resources if not item.enabled),
            "never_synced": sum(1 for item in sync_items if item["status"] == "never_synced"),
            "stale": sum(1 for item in sync_items if item["status"] == "stale"),
            "unsupported": sum(1 for item in sync_items if item["status"] == "unsupported"),
            "manual_review_required": sum(1 for item in sync_items if item["status"] == "manual_review_required"),
            "access_blocked": sum(1 for item in sync_items if item["status"] == "access_blocked"),
            "policy_excluded": sum(1 for item in sync_items if item["status"] == "policy_excluded"),
            "running": sum(1 for item in sync_items if item["status"] == "running"),
            "partial": sum(1 for item in sync_items if item["status"] == "partial"),
            "failed": sum(1 for item in sync_items if item["status"] == "failed"),
        },
        "platform_counts": dict(platform_counts),
        "resource_type_counts": dict(resource_counts),
        "sync_action_counts": dict(action_counts),
        "governance_actions": owner_actions_from_resource_items(sync_items),
        "recommendations": _monitoring_recommendations(sync_items),
    }


def resource_company_overview(db: Session, company_id: UUID | None = None) -> dict[str, Any]:
    query = select(Company).order_by(Company.name.asc())
    if company_id:
        query = query.where(Company.id == company_id)
    companies = list(db.scalars(query).all())
    return {"items": [_company_resource_overview_item(db, company) for company in companies]}


def list_v5_resources(db: Session, company_id: UUID | None = None) -> dict[str, Any]:
    query = select(Resource).order_by(Resource.created_at.desc())
    if company_id:
        query = query.where(Resource.company_id == company_id)
    items = [resource_to_v5_payload(item) for item in db.scalars(query).all()]
    return {"items": items, "counts": _count_by(items, "resource_type")}


def list_resource_sync_runs(
    db: Session,
    *,
    company_id: UUID | None = None,
    resource_id: UUID | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    query = select(ResourceSyncRun).order_by(ResourceSyncRun.started_at.desc())
    if company_id:
        query = query.where(ResourceSyncRun.company_id == company_id)
    if resource_id:
        query = query.where(ResourceSyncRun.resource_id == resource_id)
    query = query.limit(min(max(limit, 1), 100))
    return {"items": [resource_sync_run_payload(item) for item in db.scalars(query).all()]}


def resource_sync_run_payload(item: ResourceSyncRun) -> dict[str, Any]:
    payload = _run_payload(item) or {}
    payload["summary"] = item.summary or {}
    return payload


def list_workspace_event_payloads(
    db: Session,
    *,
    company_id: UUID | None = None,
    resource_id: UUID | None = None,
    limit: int = 80,
) -> dict[str, Any]:
    query = select(WorkEvent).order_by(WorkEvent.occurred_at.desc()).limit(limit)
    if company_id:
        query = query.where(WorkEvent.company_id == company_id)
    if resource_id:
        query = query.where(WorkEvent.resource_id == resource_id)
    return {"items": [workspace_event_payload(item) for item in db.scalars(query).all()]}


def workspace_event_payload(item: WorkEvent) -> dict[str, Any]:
    payload = item.payload if isinstance(item.payload, dict) else {}
    return {
        "id": str(item.id),
        "company_id": str(item.company_id),
        "resource_id": str(item.resource_id) if item.resource_id else None,
        "source": item.source,
        "event_type": item.event_type,
        "external_id": item.external_id,
        "thread_id": item.thread_id,
        "title": item.title,
        "business_domain": item.business_domain,
        "data_layer": payload.get("data_layer"),
        "sync_action": payload.get("sync_action"),
        "query_path": payload.get("query_path"),
        "occurred_at": item.occurred_at.isoformat(),
        "importance_score": item.importance_score,
        "sensitivity": item.sensitivity,
        "vector_status": item.vector_status,
    }


def _resource_sync_payload(
    resource: Resource,
    *,
    now: datetime,
    event_counts: dict[UUID, int],
    policy: dict[str, Any],
    latest_run: ResourceSyncRun | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    base = resource_to_v5_payload(resource)
    decision = sync_decision_for_resource(resource, policy)
    supported = decision["executable"] and (
        _sync_args_for_resource(resource) is not None or decision["sync_action"] == "document_index_only"
    )
    last_sync_at = resource.last_sync_at
    status = _resource_sync_status(resource, decision=decision, supported=supported, now=now, latest_run=latest_run)
    base.update(
        {
            "status": status,
            "status_order": _status_order(status),
            "sync_supported": supported,
            "sync_action": decision["sync_action"],
            "query_path": decision["query_path"],
            "authorization": decision.get("authorization"),
            "decision_status": decision["status"],
            "decision_reason": decision["reason"],
            "work_event_count": event_counts.get(resource.id, 0),
            "last_sync_age_hours": _age_hours(now, last_sync_at),
            "latest_run": _run_payload(latest_run),
            "sources": sources or [],
            "preferred_source": (sources or [None])[0],
            "next_action": _next_action(resource, status=status, supported=supported, decision=decision),
        }
    )
    return base


def _company_resource_overview_item(db: Session, company: Company) -> dict[str, Any]:
    resource_counts = dict(
        db.execute(
            select(Resource.resource_type, func.count())
            .where(Resource.company_id == company.id)
            .group_by(Resource.resource_type)
        ).all()
    )
    action_counts = dict(
        db.execute(
            select(ResourceSyncRun.sync_action, func.count())
            .where(ResourceSyncRun.company_id == company.id)
            .group_by(ResourceSyncRun.sync_action)
        ).all()
    )
    latest_run = db.scalar(
        select(ResourceSyncRun)
        .where(ResourceSyncRun.company_id == company.id)
        .order_by(ResourceSyncRun.started_at.desc())
        .limit(1)
    )
    failed_runs = int(
        db.scalar(
            select(func.count())
            .select_from(ResourceSyncRun)
            .where(ResourceSyncRun.company_id == company.id)
            .where(ResourceSyncRun.status.in_(["failed", "partial"]))
        )
        or 0
    )
    total_resources = int(
        db.scalar(select(func.count()).select_from(Resource).where(Resource.company_id == company.id)) or 0
    )
    enabled_resources = int(
        db.scalar(
            select(func.count())
            .select_from(Resource)
            .where(Resource.company_id == company.id)
            .where(Resource.enabled.is_(True))
        )
        or 0
    )
    return {
        "company_id": str(company.id),
        "company_name": company.name,
        "company_code": company.code,
        "status": getattr(company, "status", "active"),
        "resources": total_resources,
        "enabled_resources": enabled_resources,
        "failed_resource_sync_runs": failed_runs,
        "latest_sync_action": latest_run.sync_action if latest_run else None,
        "latest_sync_status": latest_run.status if latest_run else None,
        "latest_sync_at": latest_run.started_at.isoformat() if latest_run and latest_run.started_at else None,
        "resource_type_counts": {str(key): int(value) for key, value in resource_counts.items()},
        "sync_action_counts": {str(key): int(value) for key, value in action_counts.items()},
    }


def _resource_sync_status(
    resource: Resource,
    *,
    decision: dict[str, Any],
    supported: bool,
    now: datetime,
    latest_run: ResourceSyncRun | None = None,
) -> str:
    if not resource.enabled:
        return "disabled"
    if decision["status"] in {"access_blocked", "policy_excluded", "manual_review_required", "skipped"}:
        return decision["status"]
    if not supported:
        return "unsupported"
    if latest_run and latest_run.status in {"running", "partial", "failed"}:
        return latest_run.status
    if latest_run and latest_run.status == "skipped":
        return "skipped"
    if not resource.last_sync_at:
        return "never_synced"
    if now - resource.last_sync_at > STALE_AFTER:
        return "stale"
    return "healthy"


def classify_resource_sync_status(
    resource: Resource,
    *,
    now: datetime | None = None,
    policy: dict[str, Any] | None = None,
) -> str:
    current_time = now or datetime.now(UTC)
    decision = sync_decision_for_resource(resource, policy)
    supported = decision["executable"] and (
        _sync_args_for_resource(resource) is not None or decision["sync_action"] == "document_index_only"
    )
    return _resource_sync_status(resource, decision=decision, supported=supported, now=current_time)


def _status_order(status: str) -> int:
    order = {
        "failed": 0,
        "partial": 1,
        "running": 2,
        "never_synced": 3,
        "stale": 4,
        "unsupported": 5,
        "access_blocked": 6,
        "manual_review_required": 7,
        "policy_excluded": 8,
        "skipped": 9,
        "disabled": 10,
        "healthy": 11,
    }
    return order.get(status, 99)


def _age_hours(now: datetime, value: datetime | None) -> float | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return round((now - value).total_seconds() / 3600, 1)


def _next_action(resource: Resource, *, status: str, supported: bool, decision: dict[str, Any]) -> str:
    resource_type = normalize_v5_resource_type(resource.resource_type, resource.resource_id)
    if status == "disabled":
        return "如仍需使用，请先启用该资源。"
    if status in {"access_blocked", "policy_excluded", "manual_review_required", "skipped"}:
        return decision["next_action"]
    if status == "running":
        return "同步正在执行，稍后刷新同步记录。"
    if status == "partial":
        return "最近一次同步部分成功，请查看同步记录中的错误信息。"
    if status == "failed":
        return "最近一次同步失败，请查看错误信息并重新同步。"
    if not supported:
        return "当前资源类型暂未接入自动同步，请走发现或手动登记。"
    if status == "never_synced":
        return "建议先执行一次资源同步。"
    if status == "stale":
        return "建议刷新同步，避免日报和问答使用旧数据。"
    if resource_type in {"approval", "mailbox", "chat"}:
        return "保持高频同步，适合日报、待办和风险分析。"
    return "状态正常。"

def _monitoring_recommendations(items: list[dict[str, Any]]) -> list[str]:
    recommendations: list[str] = []
    if any(item["status"] == "never_synced" for item in items):
        recommendations.append("有资源从未同步，建议优先同步审批、邮箱和核心群聊。")
    if any(item["status"] == "stale" for item in items):
        recommendations.append("有资源超过 24 小时未同步，建议运行批量同步。")
    if any(item["status"] == "unsupported" for item in items):
        recommendations.append("部分资源暂未接入自动同步，系统会先做索引或等待自动发现策略成熟，避免无用大数据实时入库。")
    if any(item["status"] == "failed" for item in items):
        recommendations.append("有资源最近一次同步失败，请优先查看同步记录中的错误信息。")
    if any(item["status"] == "partial" for item in items):
        recommendations.append("有资源最近一次同步部分成功，建议确认漏同步的数据范围。")
    if any(item["status"] == "access_blocked" for item in items):
        recommendations.append("有资源因机器人未入群或授权不足被暂停自动同步，请先处理飞书成员关系或停用资源。")
    if any(item["status"] == "manual_review_required" for item in items):
        recommendations.append("有资源需要人工确认同步价值和权限，确认前不会自动拉取数据。")
    if any(item["status"] == "policy_excluded" for item in items):
        recommendations.append("有资源被当前公司同步策略排除，如需自动同步请调整资源类型范围。")
    if not recommendations:
        recommendations.append("资源同步状态正常，可以进入报告和问答能力优化。")
    return recommendations


def _resources(db: Session, company_id: UUID | None) -> list[Resource]:
    query = select(Resource).order_by(Resource.resource_type.asc(), Resource.resource_name.asc())
    if company_id:
        query = query.where(Resource.company_id == company_id)
    return list(db.scalars(query).all())


def _policies_by_company(db: Session, resources: list[Resource]) -> dict[UUID, dict[str, Any]]:
    policies: dict[UUID, dict[str, Any]] = {}
    for company_id in {resource.company_id for resource in resources}:
        policies[company_id] = get_v5_resource_sync_policy(db, company_id)
    return policies


def _work_event_counts(db: Session, company_id: UUID | None) -> dict[UUID, int]:
    query = (
        select(WorkEvent.resource_id, func.count())
        .where(WorkEvent.resource_id.is_not(None))
        .group_by(WorkEvent.resource_id)
    )
    if company_id:
        query = query.where(WorkEvent.company_id == company_id)
    return {resource_id: count for resource_id, count in db.execute(query).all() if resource_id}


def _latest_sync_runs(db: Session, company_id: UUID | None) -> list[dict[str, Any]]:
    query = select(ResourceSyncRun).order_by(ResourceSyncRun.started_at.desc())
    if company_id:
        query = query.where(ResourceSyncRun.company_id == company_id)
    query = query.limit(8)
    return [
        _run_payload(item)
        for item in db.scalars(query).all()
    ]


def _latest_sync_runs_by_resource(db: Session, company_id: UUID | None) -> dict[UUID, ResourceSyncRun]:
    query = select(ResourceSyncRun).order_by(ResourceSyncRun.started_at.desc())
    if company_id:
        query = query.where(ResourceSyncRun.company_id == company_id)

    latest: dict[UUID, ResourceSyncRun] = {}
    for item in db.scalars(query).all():
        latest.setdefault(item.resource_id, item)
    return latest


def _run_payload(run: ResourceSyncRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    rag_indexing = _run_rag_indexing(run)
    document_store = _run_document_store(run)
    return {
        "id": str(run.id),
        "company_id": str(run.company_id),
        "resource_id": str(run.resource_id),
        "sync_action": run.sync_action,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "items_seen": run.items_seen,
        "items_indexed": run.items_indexed,
        "items_skipped": run.items_skipped,
        "error_message": run.error_message,
        "rag_indexing": rag_indexing,
        "rag_indexing_summary": run_rag_indexing_summary(rag_indexing),
        "document_store": document_store,
        "document_store_summary": run_document_store_summary(document_store),
    }


def _run_rag_indexing(run: ResourceSyncRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    summary = run.summary or {}
    return summary.get("rag_indexing") if isinstance(summary.get("rag_indexing"), dict) else None


def _run_document_store(run: ResourceSyncRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    summary = run.summary or {}
    return summary.get("document_store") if isinstance(summary.get("document_store"), dict) else None


def run_rag_indexing_summary(rag_indexing: dict[str, Any] | None) -> str | None:
    if not rag_indexing:
        return None
    parts = [
        f"store={rag_indexing.get('document_store') or '-'}",
        f"chunk={rag_indexing.get('chunking') or '-'}",
        f"vector={rag_indexing.get('vector_db') or '-'}",
        f"rag={rag_indexing.get('rag_index') or '-'}",
    ]
    return " / ".join(parts)


def run_document_store_summary(document_store: dict[str, Any] | None) -> str | None:
    if not document_store:
        return None
    parts = [
        f"store={document_store.get('store') or '-'}",
        f"chunks={document_store.get('chunk_count') or 0}",
        f"events={len(document_store.get('work_event_ids') or [])}",
    ]
    return " / ".join(parts)


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts
