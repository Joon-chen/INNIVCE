from fastapi import APIRouter

from app.api.routes.v5_resource_company_overview_routes import router as resource_company_overview_router
from app.api.routes.v5_resource_list_routes import router as resource_list_router
from app.api.routes.v5_resource_sync_strategy_routes import router as resource_sync_strategy_router

router = APIRouter()

router.include_router(resource_list_router)
router.include_router(resource_company_overview_router)
router.include_router(resource_sync_strategy_router)
