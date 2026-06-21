from fastapi import APIRouter

from app.api.routes.v5_resource_direct_sync_routes import router as resource_direct_sync_router
from app.api.routes.v5_resource_retry_routes import router as resource_retry_router

router = APIRouter()

router.include_router(resource_direct_sync_router)
router.include_router(resource_retry_router)
