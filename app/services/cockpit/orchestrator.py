from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem, Report, Resource, ResourceSyncRun, WorkEvent
from app.services.cockpit.registry import DEFAULT_MODULE_ORDER, MODULE_BUILDERS
from app.services.cockpit.schemas import CockpitModuleResult, CockpitOverview
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import scope_filter


def build_scope(
    *,
    company_id: UUID | None = None,
    company_ids: list[UUID] | tuple[UUID, ...] | None = None,
    all_companies: bool = False,
) -> CockpitScope:
    if all_companies:
        return CockpitScope.all()
    if company_ids:
        return CockpitScope.multi_company(company_ids)
    return CockpitScope.single_company(company_id)


def build_cockpit_overview(
    db: Session,
    *,
    scope: CockpitScope,
    limit: int = 8,
    module_keys: list[str] | tuple[str, ...] | None = None,
) -> CockpitOverview:
    keys = module_keys or DEFAULT_MODULE_ORDER
    modules = [build_cockpit_module(db, key=key, scope=scope, limit=limit) for key in keys]
    return CockpitOverview(
        company_id=str(scope.company_id) if scope.company_id else None,
        modules=modules,
        metrics=_overview_metrics(db, scope),
    )


def build_cockpit_module(
    db: Session,
    *,
    key: str,
    scope: CockpitScope,
    limit: int = 20,
) -> CockpitModuleResult:
    builder = MODULE_BUILDERS.get(key)
    if not builder:
        allowed = ", ".join(MODULE_BUILDERS)
        raise ValueError(f"Unknown cockpit module: {key}. Allowed: {allowed}")
    return builder(db, scope=scope, limit=limit)


def list_cockpit_modules() -> list[dict[str, str]]:
    return [{"key": key, "name": MODULE_BUILDERS[key].__name__} for key in DEFAULT_MODULE_ORDER]


def _overview_metrics(db: Session, scope: CockpitScope) -> dict[str, int]:
    return {
        "work_events": _count(db, WorkEvent, scope),
        "extracted_items": _count(db, ExtractedItem, scope),
        "resources": _count(db, Resource, scope),
        "resource_sync_runs": _count(db, ResourceSyncRun, scope),
        "reports": _count(db, Report, scope),
        "vector_indexed": _count(db, WorkEvent, scope, WorkEvent.vector_status == "indexed"),
        "vector_pending": _count(db, WorkEvent, scope, WorkEvent.vector_status == "pending"),
    }


def _count(db: Session, model: object, scope: CockpitScope, *conditions: object) -> int:
    query = select(func.count()).select_from(model)
    query = scope_filter(query, model, scope)
    for condition in conditions:
        query = query.where(condition)
    return int(db.scalar(query) or 0)
