from fastapi import APIRouter

from app.api.routes.v5_resource_monitoring_routes import router as resource_monitoring_router
from app.api.routes.v5_resource_sync_run_routes import router as resource_sync_run_router
from app.api.routes.v5_resource_sync_status_list_routes import router as resource_sync_status_list_router

router = APIRouter()

router.include_router(resource_sync_status_list_router)
router.include_router(resource_sync_run_router)
router.include_router(resource_monitoring_router)
