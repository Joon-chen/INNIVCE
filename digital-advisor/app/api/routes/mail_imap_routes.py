from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.mail_request_models import ImapSyncRequest
from app.db.session import get_db
from app.services.mail_admin import sync_imap_payload

router = APIRouter()


@router.post("/imap/sync")
def sync_imap(data: ImapSyncRequest, db: Session = Depends(get_db)) -> dict[str, list[str]]:
    return sync_imap_payload(db, account_id=data.account_id, folder=data.folder, limit=data.limit)
