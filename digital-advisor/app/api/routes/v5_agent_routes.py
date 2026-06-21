from fastapi import APIRouter

from app.api.routes.v5_agent_settings_routes import router as agent_settings_router
from app.api.routes.v5_agent_trace_routes import router as agent_trace_router

router = APIRouter()

router.include_router(agent_trace_router)
router.include_router(agent_settings_router)
