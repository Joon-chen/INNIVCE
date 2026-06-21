from fastapi import APIRouter

from app.api.routes.feishu_admin_app_config_routes import router as admin_app_config_router
from app.api.routes.feishu_admin_app_oauth_routes import router as admin_app_oauth_router
from app.api.routes.feishu_admin_app_user_account_routes import router as admin_app_user_account_router

router = APIRouter()
router.include_router(admin_app_config_router)
router.include_router(admin_app_oauth_router)
router.include_router(admin_app_user_account_router)
