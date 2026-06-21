import base64
import email
import imaplib
import ssl
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Account
from app.schemas.common import WorkEventCreate
from app.services.data_classification import classify_mail_event
from app.services.data.resource_sources import EXTERNAL_MAIL_ACCOUNT
from app.services.resource_sources import upsert_mail_account_resource
from app.services.storage.minio_store import AttachmentStore
from app.services.work_events import upsert_work_event


class ImapMailClient:
    def __init__(self, account: Account) -> None:
        self.account = account
        self.credentials = account.credentials or {}

    def sync(self, db: Session, *, folder: str = "INBOX", limit: int = 20) -> list[str]:
        host = self.credentials.get("host")
        username = self.credentials.get("username") or self.account.email_address
        password = self.credentials.get("password")
        port = int(self.credentials.get("port", 993))
        use_ssl = bool(self.credentials.get("ssl", True))
        if not host or not username or not password:
            raise HTTPException(status_code=400, detail="IMAP host, username, and password are required")

        mailbox: imaplib.IMAP4 | imaplib.IMAP4_SSL
        if use_ssl:
            mailbox = imaplib.IMAP4_SSL(host, port, timeout=settings.request_timeout_seconds)
        else:
            mailbox = imaplib.IMAP4(host, port, timeout=settings.request_timeout_seconds)
        saved_ids: list[str] = []
        fetch_errors: list[str] = []
        try:
            mailbox.login(username, password)
            mailbox.select(folder)
            status, ids_data = mailbox.search(None, "ALL")
            if status != "OK":
                raise HTTPException(status_code=502, detail="IMAP search failed")
            message_ids = ids_data[0].split()[-limit:]
            for message_id in message_ids:
                try:
                    status, msg_data = mailbox.fetch(message_id, "(RFC822)")
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error, ssl.SSLError, OSError) as exc:
                    fetch_errors.append(f"{message_id.decode(errors='ignore')}: {exc}")
                    break
                if status != "OK" or not msg_data:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, bytes):
                    continue
                work_event = ingest_email_message(db, self.account, raw, labels=[folder])
                saved_ids.append(str(work_event.id))
            db.commit()
            if fetch_errors and not saved_ids:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "message": "IMAP fetch failed before any message was saved",
                        "errors": fetch_errors,
                        "hint": "Try a smaller limit, another folder, or verify IMAP client access/password.",
                    },
                )
            return saved_ids
        except HTTPException:
            db.rollback()
            raise
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, ssl.SSLError, OSError) as exc:
            db.rollback()
            raise HTTPException(status_code=502, detail={"message": "IMAP sync failed", "error": str(exc)})
        finally:
            try:
                mailbox.close()
            except Exception:
                pass
            try:
                mailbox.logout()
            except Exception:
                pass


def ingest_email_message(db: Session, account: Account, raw_message: bytes, labels: list[str]) -> Any:
    msg = email.message_from_bytes(raw_message)
    subject = _header(msg, "Subject") or "(no subject)"
    message_id = _header(msg, "Message-ID") or base64.urlsafe_b64encode(raw_message[:64]).decode()
    thread_id = _header(msg, "Thread-Index") or _header(msg, "References") or message_id
    occurred_at = _parse_date(_header(msg, "Date"))
    text_body = _extract_text(msg)
    folder_id = labels[0] if labels else str((account.settings or {}).get("folder_id") or "INBOX")
    resource = upsert_mail_account_resource(db, account, folder_id=folder_id)
    headers = {
        "from": _header(msg, "From"),
        "to": _header(msg, "To"),
        "cc": _header(msg, "Cc"),
        "date": _header(msg, "Date"),
        "message_id": message_id,
        "references": _header(msg, "References"),
        "in_reply_to": _header(msg, "In-Reply-To"),
    }
    classification = classify_mail_event(
        account_settings=account.settings or {},
        subject=subject,
        text_body=text_body,
        headers=headers,
        labels=labels,
    )

    event = upsert_work_event(
        db,
        WorkEventCreate(
            company_id=account.company_id,
            account_id=account.id,
            resource_id=resource.id,
            source=account.provider,
            source_type=EXTERNAL_MAIL_ACCOUNT,
            source_account_id=str(account.id),
            visibility_scope=classification.visibility_scope,
            allowed_user_ids=(account.settings or {}).get("allowed_user_ids") or [],
            allowed_roles=(account.settings or {}).get("allowed_roles") or [],
            allowed_departments=(account.settings or {}).get("allowed_departments") or [],
            data_classification=classification.data_classification,
            business_domain=classification.business_domain,
            event_type="mail.message",
            external_id=message_id,
            thread_id=thread_id,
            title=subject,
            content_text=text_body,
            occurred_at=occurred_at,
            actors=[
                {"role": "from", "value": _header(msg, "From")},
                {"role": "to", "value": _header(msg, "To")},
                {"role": "cc", "value": _header(msg, "Cc")},
            ],
            labels=["mail", account.provider, *labels],
            payload={
                "headers": headers,
                "classification": {
                    "data_classification": classification.data_classification,
                    "business_domain": classification.business_domain,
                    "visibility_scope": classification.visibility_scope,
                    "reason": classification.reason,
                },
            },
        ),
    )
    db.flush()
    _save_email_attachments(db, event.id, msg)
    return event


class GmailOAuthService:
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.labels",
    ]

    def build_authorization_url(self, state: str, *, redirect_uri: str | None = None) -> str:
        if not settings.gmail_client_id or not settings.gmail_client_secret:
            raise HTTPException(status_code=400, detail="Gmail OAuth env vars are not configured")
        params = urlencode(
            {
                "client_id": settings.gmail_client_id,
                "redirect_uri": redirect_uri or settings.gmail_redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.scopes),
                "access_type": "offline",
                "include_granted_scopes": "true",
                "state": state,
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


class GraphMailService:
    def build_authorization_url(self, state: str, *, redirect_uri: str | None = None) -> str:
        if not settings.ms_graph_client_id:
            raise HTTPException(status_code=400, detail="Microsoft Graph env vars are not configured")
        params = urlencode(
            {
                "client_id": settings.ms_graph_client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri or settings.ms_graph_redirect_uri,
                "response_mode": "query",
                "scope": " ".join(["offline_access", "User.Read", "Mail.Read"]),
                "state": state,
            }
        )
        tenant = settings.ms_graph_tenant_id
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{params}"

    async def fetch_messages(self, access_token: str, limit: int = 20) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"$top": limit},
            )
            response.raise_for_status()
            return response.json()


def _header(msg: Message, key: str) -> str | None:
    value = msg.get(key)
    if value is None:
        return None
    return str(email.header.make_header(email.header.decode_header(value)))


def _parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _extract_text(msg: Message) -> str:
    if msg.is_multipart():
        parts: list[str] = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart" or part.get_filename():
                continue
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    parts.append(payload.decode(charset, errors="replace"))
        return "\n".join(parts).strip()
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace").strip()
    return str(msg.get_payload() or "").strip()


def _save_email_attachments(db: Session, work_event_id, msg: Message) -> None:
    store = AttachmentStore()
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        store.save_attachment(
            db,
            work_event_id=work_event_id,
            filename=str(email.header.make_header(email.header.decode_header(filename))),
            content_type=part.get_content_type(),
            data=payload,
        )


def get_account_or_404(db: Session, account_id) -> Account:
    account = db.get(Account, account_id)
    if not account or not account.is_active:
        raise HTTPException(status_code=404, detail="Account not found")
    return account
