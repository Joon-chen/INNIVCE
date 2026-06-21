from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import Account
from app.schemas.common import AccountCreate, AccountOut
from app.services.companies_admin import create_account_payload

router = APIRouter()


@router.post("/accounts", response_model=AccountOut)
def create_account(data: AccountCreate, db: Session = Depends(get_db)) -> Account:
    return create_account_payload(db, data)
