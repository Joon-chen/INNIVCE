from fastapi import APIRouter

from app.services.mail_admin import graph_messages_payload

router = APIRouter()


@router.get("/graph/messages")
async def graph_messages(access_token: str, limit: int = 20) -> dict:
    return await graph_messages_payload(access_token=access_token, limit=limit)
