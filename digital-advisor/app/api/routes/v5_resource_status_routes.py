from fastapi import APIRouter

from app.api.routes.v5_resource_overview_routes import router as resource_overview_router
from app.api.routes.v5_resource_sync_status_routes import router as resource_sync_status_router
from app.api.routes.v5_workspace_event_routes import router as workspace_event_router

router = APIRouter()

router.include_router(resource_overview_router)
router.include_router(resource_sync_status_router)
router.include_router(workspace_event_router)
