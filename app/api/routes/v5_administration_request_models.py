from uuid import UUID

from pydantic import BaseModel, Field


class AccessPreviewRequest(BaseModel):
    company_id: UUID
    resource_id: UUID
    user_id: UUID | None = None
    open_id: str | None = None
    role: str | None = None
    domains: list[str] = Field(default_factory=list)
    current_chat_id: str | None = None
