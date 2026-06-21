from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin_api_token
from app.api.routes.v5_administration_routes import router as administration_router
from app.api.routes.v5_agent_routes import router as agent_router
from app.api.routes.v5_intelligence_routes import router as intelligence_router
from app.api.routes.v5_resource_routes import router as resource_router
from app.api.routes.v5_system_log_routes import router as system_log_router
from app.api.routes.v5_tool_routes import router as tool_router

router = APIRouter(prefix="/api/v5", tags=["v5"], dependencies=[Depends(require_admin_api_token)])
router.include_router(administration_router)
router.include_router(agent_router)
router.include_router(intelligence_router)
router.include_router(resource_router)
router.include_router(system_log_router)
router.include_router(tool_router)
