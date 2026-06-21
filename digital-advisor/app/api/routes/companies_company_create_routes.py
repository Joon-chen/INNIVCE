from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Company
from app.schemas.common import CompanyCreate, CompanyOut
from app.services.companies_admin import create_company_payload

router = APIRouter()


@router.post("/companies", response_model=CompanyOut)
def create_company(data: CompanyCreate, db: Session = Depends(get_db)) -> Company:
    return create_company_payload(db, data)
