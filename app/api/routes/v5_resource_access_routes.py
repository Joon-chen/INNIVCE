from fastapi import APIRouter

from app.api.routes.v5_resource_access_decision_routes import (
    router as resource_access_decision_router,
)
from app.api.routes.v5_resource_clear_access_block_routes import router as resource_clear_access_block_router

router = APIRouter()

router.include_router(resource_clear_access_block_router)
router.include_router(resource_access_decision_router)
