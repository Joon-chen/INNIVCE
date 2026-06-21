from fastapi import APIRouter

from app.api.routes.v5_resource_policy_read_routes import router as resource_policy_read_router
from app.api.routes.v5_resource_policy_write_routes import router as resource_policy_write_router

router = APIRouter()

router.include_router(resource_policy_read_router)
router.include_router(resource_policy_write_router)
