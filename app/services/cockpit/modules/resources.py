from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Resource, ResourceSyncRun
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import scope_filter
from app.services.v5_owner_actions import owner_actions_from_resource_items
from app.services.v5_sync_decisions import sync_decision_for_resource
from app.services.v5_sync_policy import get_v5_resource_sync_policy


STALE_AFTER = timedelta(hours=24)


def build_resources_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    resources = _resources(db, scope)
    latest_runs = _latest_sync_runs_by_resource(db, scope)
    resource_items = [
        _resource_health_payload(db, resource=resource, latest_run=latest_runs.get(resource.id))
        for resource in resources
    ]
    issue_items = [item for item in resource_items if item["owner_visible"]]
    owner_actions = owner_actions_from_resource_items(issue_items)
    owner_action_items = [
        CockpitItem(
            id=item["action_code"],
            title=_owner_action_title(item),
            subtitle=item["business_impact"],
            description=item.get("owner_next_step_detail") or item["owner_next_step"],
            status="待处理",
            priority="high",
            owner=item.get("responsible_role") or "系统管理员",
            payload={
                "count": item["count"],
                "business_domain": item.get("business_domain"),
                "responsible_role": item.get("responsible_role"),
                "system_behavior": item["system_behavior"],
                "access_recommendation_summary": item.get("access_recommendation_summary"),
                "recommended_notify_targets": item.get("recommended_notify_targets") or [],
                "resources": item["resources"],
            },
        )
        for item in owner_actions[:limit]
    ]
    total_count = len(resources)
    enabled_count = sum(1 for item in resources if item.enabled)
    counts = _status_counts(resource_items)
    sync_action_counts = _sync_action_counts_from_items(resource_items)
    resource_type_counts = _resource_type_counts_from_resources(resources)
    automatic_ok = counts.get("healthy", 0)
    pending_auto = counts.get("never_synced", 0) + counts.get("stale", 0)
    blindspots = counts.get("access_blocked", 0) + counts.get("manual_review_required", 0)
    sync_errors = counts.get("failed", 0) + counts.get("partial", 0)
    return CockpitModuleResult(
        key="resources",
        name="数据覆盖",
        description="老板驾驶舱能看到哪些飞书经营数据，以及哪些数据暂时不可见。",
        count=enabled_count,
        summary=(
            f"当前启用 {enabled_count} 项数据来源；"
            f"自动同步正常 {automatic_ok} 项、等待自动同步 {pending_auto} 项、数据盲区 {blindspots} 项、同步异常 {sync_errors} 项。"
        ),
        items=owner_action_items,
        next_actions=_resource_next_actions(
            blindspots=blindspots,
            sync_errors=sync_errors,
            pending_auto=pending_auto,
            policy_excluded=counts.get("policy_excluded", 0),
        ),
        metrics={
            "resources": total_count,
            "enabled": enabled_count,
            "auto_sync_healthy": automatic_ok,
            "auto_sync_pending": pending_auto,
            "data_blindspots": blindspots,
            "sync_errors": sync_errors,
            "access_blocked": counts.get("access_blocked", 0),
            "manual_review_required": counts.get("manual_review_required", 0),
            "policy_excluded": counts.get("policy_excluded", 0),
            "latest_resource_sync_runs": len(latest_runs),
            "resource_sync_statuses": counts,
            "sync_actions": sync_action_counts,
            "resource_types": resource_type_counts,
            "owner_actions": owner_actions,
        },
    )


def _resources(db: Session, scope: CockpitScope) -> list[Resource]:
    query = select(Resource).order_by(Resource.resource_type.asc(), Resource.resource_name.asc())
    query = scope_filter(query, Resource, scope)
    return list(db.scalars(query).all())


def _latest_sync_runs_by_resource(db: Session, scope: CockpitScope) -> dict[object, ResourceSyncRun]:
    query = select(ResourceSyncRun).order_by(ResourceSyncRun.started_at.desc())
    query = scope_filter(query, ResourceSyncRun, scope)
    latest: dict[object, ResourceSyncRun] = {}
    for run in db.scalars(query).all():
        if run.resource_id in latest:
            continue
        latest[run.resource_id] = run
    return latest


def _resource_health_payload(db: Session, *, resource: Resource, latest_run: ResourceSyncRun | None) -> dict:
    policy = get_v5_resource_sync_policy(db, resource.company_id)
    decision = sync_decision_for_resource(resource, policy)
    status = _resource_health_status(resource, decision=decision, latest_run=latest_run)
    rag_indexing = _run_rag_indexing(latest_run)
    document_store = _run_document_store(latest_run)
    return {
        "id": str(resource.id),
        "resource_name": resource.resource_name,
        "resource_id": resource.resource_id,
        "resource_type": decision["resource_type"],
        "status": status,
        "sync_action": decision["sync_action"],
        "query_path": decision["query_path"],
        "rag_indexing": rag_indexing,
        "rag_indexing_summary": run_rag_indexing_summary(rag_indexing),
        "document_store": document_store,
        "document_store_summary": run_document_store_summary(document_store),
        "decision_status": decision["status"],
        "next_action": _resource_health_next_action(status, decision=decision),
        "identifier_issue": decision.get("identifier_issue"),
        "owner_visible": status in {
            "access_blocked",
            "manual_review_required",
            "policy_excluded",
            "failed",
            "partial",
        },
        "config_json": {"governance": _governance(resource)},
    }


def _run_rag_indexing(run: ResourceSyncRun | None) -> dict | None:
    if not run:
        return None
    summary = run.summary or {}
    return summary.get("rag_indexing") if isinstance(summary.get("rag_indexing"), dict) else None


def _run_document_store(run: ResourceSyncRun | None) -> dict | None:
    if not run:
        return None
    summary = run.summary or {}
    return summary.get("document_store") if isinstance(summary.get("document_store"), dict) else None


def run_rag_indexing_summary(rag_indexing: dict | None) -> str | None:
    if not rag_indexing:
        return None
    parts = [
        f"store={rag_indexing.get('document_store') or '-'}",
        f"chunk={rag_indexing.get('chunking') or '-'}",
        f"vector={rag_indexing.get('vector_db') or '-'}",
        f"rag={rag_indexing.get('rag_index') or '-'}",
    ]
    return " / ".join(parts)


def run_document_store_summary(document_store: dict | None) -> str | None:
    if not document_store:
        return None
    parts = [
        f"store={document_store.get('store') or '-'}",
        f"chunks={document_store.get('chunk_count') or 0}",
        f"events={len(document_store.get('work_event_ids') or [])}",
    ]
    return " / ".join(parts)


def _resource_health_status(
    resource: Resource,
    *,
    decision: dict,
    latest_run: ResourceSyncRun | None,
) -> str:
    if not resource.enabled:
        return "disabled"
    if decision["status"] in {"access_blocked", "manual_review_required", "policy_excluded", "skipped"}:
        return decision["status"]
    if latest_run and latest_run.status in {"running", "partial", "failed"}:
        return latest_run.status
    if not resource.last_sync_at:
        return "never_synced"
    last_sync_at = resource.last_sync_at
    if last_sync_at.tzinfo is None:
        last_sync_at = last_sync_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - last_sync_at > STALE_AFTER:
        return "stale"
    return "healthy"


def _resource_health_next_action(status: str, *, decision: dict) -> str:
    if status in {"access_blocked", "manual_review_required", "policy_excluded", "skipped"}:
        return decision["next_action"]
    if status == "failed":
        return "系统会在下一轮自动同步中重试；连续失败再查看设置页诊断。"
    if status == "partial":
        return "系统会继续补齐数据；如影响日报和问答，再查看设置页诊断。"
    if status in {"never_synced", "stale"}:
        return "系统会按公司自动同步策略处理。"
    return "状态正常。"


def _owner_action_title(item: dict) -> str:
    count = int(item.get("count") or 0)
    return f"{item['title']}（{count}项）" if count else item["title"]


def _status_counts(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get("status") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _sync_action_counts_from_items(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get("sync_action") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _resource_type_counts_from_resources(resources: list[Resource]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for resource in resources:
        key = str(resource.resource_type or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _resource_next_actions(
    *,
    blindspots: int,
    sync_errors: int,
    pending_auto: int,
    policy_excluded: int,
) -> list[str]:
    actions: list[str] = []
    if blindspots:
        actions.append(f"优先处理 {blindspots} 个会影响分析完整性的数据盲区。")
    if sync_errors:
        actions.append(f"{sync_errors} 个数据源最近同步不完整，系统会自动重试；连续失败再进设置页查看。")
    if pending_auto:
        actions.append(f"{pending_auto} 个数据源等待自动同步，通常不需要手工处理。")
    if policy_excluded:
        actions.append(f"{policy_excluded} 个数据源被策略排除，确认有经营价值后再纳入。")
    if not actions:
        actions.append("数据覆盖状态正常，可以继续优化报告和机器人问答质量。")
    actions.append("技术细节放在设置页处理，驾驶舱只展示对经营判断有影响的动作。")
    return actions


def _governance(resource: Resource) -> dict:
    config = resource.config_json if isinstance(resource.config_json, dict) else {}
    governance = config.get("governance") or {}
    return governance if isinstance(governance, dict) else {}
