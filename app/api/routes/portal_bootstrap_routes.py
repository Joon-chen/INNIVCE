from typing import Any

from fastapi import APIRouter

from app.services.portal_runtime import portal_bootstrap_payload


router = APIRouter()


@router.get("/api/portal/bootstrap")
def portal_bootstrap(chat_id: str | None = None) -> dict[str, Any]:
    return portal_bootstrap_payload(chat_id)
