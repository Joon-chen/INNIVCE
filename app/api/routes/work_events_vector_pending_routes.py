from fastapi import APIRouter

from app.api.routes.work_events_request_models import VectorizePendingRequest
from app.services.work_events_admin import vectorize_pending_payload

router = APIRouter()


@router.post("/vector/index-pending")
def vectorize_pending(data: VectorizePendingRequest) -> dict:
    return vectorize_pending_payload(data)
