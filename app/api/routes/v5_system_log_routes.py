from fastapi import APIRouter

from app.api.routes.v5_system_log_list_routes import router as system_log_list_router
from app.api.routes.v5_system_log_overview_routes import router as system_log_overview_router

router = APIRouter()

router.include_router(system_log_overview_router)
router.include_router(system_log_list_router)
