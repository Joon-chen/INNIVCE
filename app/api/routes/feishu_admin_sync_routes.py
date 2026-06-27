from fastapi import APIRouter

from app.api.routes.feishu_admin_sync_information_routes import router as sync_information_router
from app.api.routes.feishu_admin_sync_message_routes import router as sync_message_router
from app.api.routes.feishu_admin_sync_organization_routes import router as sync_organization_router
from app.api.routes.feishu_admin_sync_resource_routes import router as sync_resource_router

router = APIRouter()

router.include_router(sync_message_router)
router.include_router(sync_information_router)
router.include_router(sync_organization_router)
router.include_router(sync_resource_router)
