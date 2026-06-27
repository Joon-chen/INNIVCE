from typing import Any

from fastapi import APIRouter

from app.services.portal_runtime import portal_result_context_payload


router = APIRouter()


@router.get("/api/portal/result-context")
def portal_result_context(chat_id: str | None = None) -> dict[str, Any]:
    return portal_result_context_payload(chat_id)
