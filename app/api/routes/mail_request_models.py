from uuid import UUID

from pydantic import BaseModel, Field


class ImapSyncRequest(BaseModel):
    account_id: UUID
    folder: str = "INBOX"
    limit: int = Field(default=20, ge=1, le=200)
