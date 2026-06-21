from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.feishu_oauth_helpers import feishu_oauth_callback_payload

router = APIRouter()


@router.get("/oauth/callback")
async def feishu_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return await feishu_oauth_callback_payload(db, code=code, state=state)
