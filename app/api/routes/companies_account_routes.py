from fastapi import APIRouter

from app.api.routes.companies_account_create_routes import router as account_create_router
from app.api.routes.companies_account_list_routes import router as account_list_router

router = APIRouter()

router.include_router(account_create_router)
router.include_router(account_list_router)
