from fastapi import APIRouter

from app.api.routes.feishu_admin_app_oauth_exchange_routes import router as admin_app_oauth_exchange_router
from app.api.routes.feishu_admin_app_oauth_url_routes import router as admin_app_oauth_url_router

router = APIRouter()

router.include_router(admin_app_oauth_url_router)
router.include_router(admin_app_oauth_exchange_router)
