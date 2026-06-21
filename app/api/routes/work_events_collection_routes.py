from fastapi import APIRouter

from app.api.routes.work_events_create_routes import router as work_events_create_router
from app.api.routes.work_events_detail_routes import router as work_events_detail_router
from app.api.routes.work_events_list_routes import router as work_events_list_router

router = APIRouter()

router.include_router(work_events_create_router)
router.include_router(work_events_list_router)
router.include_router(work_events_detail_router)
