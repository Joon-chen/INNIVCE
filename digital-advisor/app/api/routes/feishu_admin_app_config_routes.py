from fastapi import APIRouter

from app.api.routes.feishu_admin_app_client_routing_routes import router as admin_app_client_routing_router
from app.api.routes.feishu_admin_app_create_routes import router as admin_app_create_router
from app.api.routes.feishu_admin_app_tenant_token_routes import router as admin_app_tenant_token_router

router = APIRouter()

router.include_router(admin_app_create_router)
router.include_router(admin_app_tenant_token_router)
router.include_router(admin_app_client_routing_router)
