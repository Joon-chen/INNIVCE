from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.routes.cockpit_query_params import CompanyIdsQuery
from app.db.session import get_db
from app.services.cockpit_admin import cockpit_module_payload

router = APIRouter()


@router.get("/modules/{module_key}")
def cockpit_module(
    module_key: str,
    company_id: UUID | None = None,
    company_ids: CompanyIdsQuery = None,
    all_companies: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    return cockpit_module_payload(
        db,
        module_key=module_key,
        company_id=company_id,
        company_ids=company_ids,
        all_companies=all_companies,
        limit=limit,
    )
