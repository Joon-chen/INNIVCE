from fastapi import APIRouter

from app.api.routes.v5_resource_policy_routes import router as resource_policy_router
from app.api.routes.v5_resource_status_routes import router as resource_status_router
from app.api.routes.v5_resource_sync_routes import router as resource_sync_router
from app.api.routes.v5_resource_workspace_routes import router as resource_workspace_router

router = APIRouter()
router.include_router(resource_policy_router)
router.include_router(resource_status_router)
router.include_router(resource_sync_router)
router.include_router(resource_workspace_router)
