from uuid import UUID

from sqlalchemy.orm import Session

from app.services.integrations.mail import GmailOAuthService, GraphMailService, ImapMailClient, get_account_or_404
from app.services.sync_runs import finish_sync_run, start_sync_run


def sync_imap_payload(db: Session, *, account_id: UUID, folder: str, limit: int) -> dict[str, list[str]]:
    account = get_account_or_404(db, account_id)
    sync_run = start_sync_run(
        db,
        company_id=account.company_id,
        provider="imap",
        sync_type="manual",
        cursor={"account_id": str(account.id), "folder": folder},
    )
    try:
        saved_ids = ImapMailClient(account).sync(db, folder=folder, limit=limit)
        finish_sync_run(sync_run, status="success", saved_count=len(saved_ids), summary={"work_event_ids": saved_ids})
    except Exception as exc:
        finish_sync_run(sync_run, status="failed", error_count=1, summary={"error": str(exc)[:500]})
        db.commit()
        raise
    db.commit()
    return {"work_event_ids": saved_ids}


def gmail_oauth_url_payload(*, state: str) -> dict[str, str]:
    return {"authorization_url": GmailOAuthService().build_authorization_url(state=state)}


def graph_oauth_url_payload(*, state: str) -> dict[str, str]:
    return {"authorization_url": GraphMailService().build_authorization_url(state=state)}


async def graph_messages_payload(*, access_token: str, limit: int) -> dict:
    return await GraphMailService().fetch_messages(access_token=access_token, limit=limit)
