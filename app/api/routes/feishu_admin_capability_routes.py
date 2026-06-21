from fastapi import APIRouter

from app.api.routes.feishu_admin_capability_approval_resource_routes import (
    router as admin_capability_approval_resource_router,
)
from app.api.routes.feishu_admin_capability_probe_routes import router as admin_capability_probe_router
from app.api.routes.feishu_admin_capability_sync_plan_routes import router as admin_capability_sync_plan_router

router = APIRouter()

router.include_router(admin_capability_sync_plan_router)
router.include_router(admin_capability_probe_router)
router.include_router(admin_capability_approval_resource_router)
