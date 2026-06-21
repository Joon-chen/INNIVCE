from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.cockpit import build_cockpit_module, build_cockpit_overview, build_scope


def cockpit_overview_payload(
    db: Session,
    *,
    company_id: UUID | None = None,
    company_ids: list[UUID] | None = None,
    all_companies: bool = False,
    limit: int = 8,
) -> dict:
    scope = build_scope(company_id=company_id, company_ids=company_ids, all_companies=all_companies)
    return build_cockpit_overview(db, scope=scope, limit=limit).model_dump()


def cockpit_module_payload(
    db: Session,
    *,
    module_key: str,
    company_id: UUID | None = None,
    company_ids: list[UUID] | None = None,
    all_companies: bool = False,
    limit: int = 50,
) -> dict:
    scope = build_scope(company_id=company_id, company_ids=company_ids, all_companies=all_companies)
    try:
        return build_cockpit_module(db, key=module_key, scope=scope, limit=limit).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
