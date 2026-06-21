from fastapi import APIRouter

from app.api.routes.v5_agent_settings_read_routes import router as agent_settings_read_router
from app.api.routes.v5_agent_settings_write_routes import router as agent_settings_write_router

router = APIRouter()

router.include_router(agent_settings_read_router)
router.include_router(agent_settings_write_router)
