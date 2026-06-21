from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.companies_request_models import QuickCompanySetupRequest
from app.db.session import get_db
from app.services.companies_admin import quick_company_setup_payload

router = APIRouter()


@router.post("/onboarding/company-setup")
def quick_company_setup(data: QuickCompanySetupRequest, db: Session = Depends(get_db)) -> dict:
    return quick_company_setup_payload(db, data)
