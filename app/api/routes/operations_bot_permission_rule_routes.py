from typing import Any

from fastapi import APIRouter

from app.services.operations_bot_users import bot_permission_rules_payload

router = APIRouter()


@router.get("/bot-permission-rules")
def list_bot_permission_rules() -> dict[str, Any]:
    return bot_permission_rules_payload()
