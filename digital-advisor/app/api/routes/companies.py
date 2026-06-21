from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin_api_token
from app.api.routes.companies_account_routes import router as company_account_router
from app.api.routes.companies_company_routes import router as company_router
from app.api.routes.companies_onboarding_routes import router as company_onboarding_router

router = APIRouter(prefix="/api", tags=["companies"], dependencies=[Depends(require_admin_api_token)])
router.include_router(company_account_router)
router.include_router(company_onboarding_router)
router.include_router(company_router)
