from fastapi import APIRouter

from app.api.routes.v5_tool_catalog_routes import router as tool_catalog_router
from app.api.routes.v5_tool_execution_log_routes import router as tool_execution_log_router

router = APIRouter()

router.include_router(tool_catalog_router)
router.include_router(tool_execution_log_router)
