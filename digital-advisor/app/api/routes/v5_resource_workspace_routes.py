from fastapi import APIRouter

from app.api.routes.v5_resource_access_routes import router as resource_access_router
from app.api.routes.v5_resource_single_sync_routes import router as resource_single_sync_router

router = APIRouter()

router.include_router(resource_single_sync_router)
router.include_router(resource_access_router)
