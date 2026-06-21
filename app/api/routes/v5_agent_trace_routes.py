from fastapi import APIRouter

from app.api.routes.v5_agent_trace_list_routes import router as agent_trace_list_router
from app.api.routes.v5_agent_trace_preview_routes import router as agent_trace_preview_router
from app.api.routes.v5_agent_reply_mode_routes import router as agent_reply_mode_router

router = APIRouter()

router.include_router(agent_reply_mode_router)
router.include_router(agent_trace_preview_router)
router.include_router(agent_trace_list_router)
