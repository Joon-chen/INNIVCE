from typing import Any

from fastapi import APIRouter

from app.services.v5_sync_strategy import sync_strategy_overview

router = APIRouter()


@router.get("/resources/sync-strategy")
def resource_sync_strategy() -> dict[str, Any]:
    return sync_strategy_overview()
