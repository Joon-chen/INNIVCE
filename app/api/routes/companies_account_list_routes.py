from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Account
from app.schemas.common import AccountOut
from app.services.companies_admin import list_company_account_payloads

router = APIRouter()


@router.get("/companies/{company_id}/accounts", response_model=list[AccountOut])
def list_company_accounts(company_id: UUID, db: Session = Depends(get_db)) -> list[Account]:
    return list_company_account_payloads(db, company_id)
