from fastapi import APIRouter

from app.api.routes.v5_tool_batch_config_routes import router as tool_batch_config_router
from app.api.routes.v5_tool_single_config_routes import router as tool_single_config_router

router = APIRouter()

router.include_router(tool_single_config_router)
router.include_router(tool_batch_config_router)
