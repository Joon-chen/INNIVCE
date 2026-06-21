from fastapi import APIRouter

from app.api.routes.companies_company_create_routes import router as company_create_router
from app.api.routes.companies_company_list_routes import router as company_list_router

router = APIRouter()

router.include_router(company_create_router)
router.include_router(company_list_router)
