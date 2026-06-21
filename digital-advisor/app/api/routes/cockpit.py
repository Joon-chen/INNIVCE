from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin_api_token
from app.api.routes.cockpit_module_routes import router as cockpit_module_router
from app.api.routes.cockpit_overview_routes import router as cockpit_overview_router

router = APIRouter(prefix="/api/cockpit", tags=["cockpit"], dependencies=[Depends(require_admin_api_token)])
router.include_router(cockpit_module_router)
router.include_router(cockpit_overview_router)
