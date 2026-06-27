from datetime import datetime

from pydantic import BaseModel, Field


class FeishuSyncMessagesRequest(BaseModel):
    chat_id: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    page_size: int = Field(default=20, ge=1, le=50)
    max_pages: int = Field(default=3, ge=1, le=20)
    extract_items: bool = False


class FeishuInformationSyncRequest(BaseModel):
    kinds: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)
    max_pages: int = Field(default=3, ge=1, le=20)
    chat_id: str | None = None
    user_mailbox_id: str | None = None
    folder_id: str | None = None
    app_token: str | None = None
    table_id: str | None = None
    approval_code: str | None = None
    document_id: str | None = None
    document_type: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    extract_items: bool = False


class FeishuOrganizationSyncRequest(BaseModel):
    sync_type: str = "full"
    max_departments: int = Field(default=500, ge=1, le=1000)
    max_users: int = Field(default=2000, ge=1, le=5000)


class FeishuResourceDiscoverRequest(BaseModel):
    kinds: list[str] = Field(default_factory=list)
    async_run: bool = True
    mailbox_id: str | None = None
    mailbox_ids: list[str] = Field(default_factory=list)
    app_tokens: list[str] = Field(default_factory=list)
    bitable_tables: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    wiki_space_ids: list[str] = Field(default_factory=list)
    folder_tokens: list[str] = Field(default_factory=list)
    docs_search_keywords: list[str] = Field(default_factory=list)
    approval_codes: list[str] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=100)
    include_local_mining: bool = True
