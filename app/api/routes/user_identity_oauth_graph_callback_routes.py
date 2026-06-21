from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.routes.user_identity_oauth_helpers import mail_user_identity_oauth_callback
from app.db.session import get_db

router = APIRouter()


@router.get("/mail/graph/callback")
def graph_user_identity_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return mail_user_identity_oauth_callback(db, code=code, state=state, expected_provider="graph")
