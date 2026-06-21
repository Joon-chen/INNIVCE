from fastapi import APIRouter

from app.api.routes.v5_administration_company_setting_routes import router as administration_company_setting_router
from app.api.routes.v5_administration_user_routes import router as administration_user_router

router = APIRouter()

router.include_router(administration_user_router)
router.include_router(administration_company_setting_router)
