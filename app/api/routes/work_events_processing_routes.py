from fastapi import APIRouter

from app.api.routes.work_events_extract_routes import router as work_events_extract_router
from app.api.routes.work_events_vectorize_routes import router as work_events_vectorize_router

router = APIRouter()

router.include_router(work_events_extract_router)
router.include_router(work_events_vectorize_router)
