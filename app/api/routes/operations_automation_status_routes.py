from typing import Any

from fastapi import APIRouter

from app.services.operations_status import automation_status_payload

router = APIRouter()


@router.get("/automation/status")
def automation_status() -> dict[str, Any]:
    return automation_status_payload()
