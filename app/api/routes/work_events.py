from fastapi import APIRouter, Depends

from app.api.dependencies import require_admin_api_token
from app.api.routes.work_events_analysis_routes import router as work_events_analysis_router
from app.api.routes.work_events_collection_routes import router as work_events_collection_router
from app.api.routes.work_events_processing_routes import router as work_events_processing_router

router = APIRouter(prefix="/api", tags=["work-events"], dependencies=[Depends(require_admin_api_token)])
router.include_router(work_events_analysis_router)
router.include_router(work_events_collection_router)
router.include_router(work_events_processing_router)
