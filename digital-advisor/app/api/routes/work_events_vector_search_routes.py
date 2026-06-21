from fastapi import APIRouter

from app.api.routes.work_events_request_models import VectorSearchRequest
from app.services.work_events_admin import vector_search_payload

router = APIRouter()


@router.post("/vector/search")
def vector_search(data: VectorSearchRequest) -> dict:
    return vector_search_payload(data)
