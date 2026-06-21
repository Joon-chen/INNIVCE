from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Company
from app.schemas.common import CompanyOut
from app.services.companies_admin import list_company_payloads

router = APIRouter()


@router.get("/companies", response_model=list[CompanyOut])
def list_companies(db: Session = Depends(get_db)) -> list[Company]:
    return list_company_payloads(db)
