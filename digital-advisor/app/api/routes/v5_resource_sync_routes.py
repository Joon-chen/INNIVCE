from fastapi import APIRouter

from app.api.routes.v5_resource_batch_execute_routes import router as resource_batch_execute_router
from app.api.routes.v5_resource_batch_preview_routes import router as resource_batch_preview_router

router = APIRouter()

router.include_router(resource_batch_preview_router)
router.include_router(resource_batch_execute_router)
