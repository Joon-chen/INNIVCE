from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.routes.cockpit_query_params import CompanyIdsQuery
from app.db.session import get_db
from app.services.cockpit_admin import cockpit_overview_payload

router = APIRouter()


@router.get("/overview")
def cockpit_overview(
    company_id: UUID | None = None,
    company_ids: CompanyIdsQuery = None,
    all_companies: bool = False,
    limit: int = Query(default=8, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict:
    return cockpit_overview_payload(
        db,
        company_id=company_id,
        company_ids=company_ids,
        all_companies=all_companies,
        limit=limit,
    )
