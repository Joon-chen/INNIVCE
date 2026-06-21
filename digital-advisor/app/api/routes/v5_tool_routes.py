from fastapi import APIRouter

from app.api.routes.v5_tool_config_routes import router as tool_config_router
from app.api.routes.v5_tool_execution_routes import router as tool_execution_router
from app.api.routes.v5_tool_list_routes import router as tool_list_router

router = APIRouter()
router.include_router(tool_config_router)
router.include_router(tool_execution_router)
router.include_router(tool_list_router)
