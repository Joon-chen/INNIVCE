from uuid import UUID

from pydantic import BaseModel, Field


class FeishuCliUserAuthStartRequest(BaseModel):
    company_id: UUID
    open_id: str = Field(min_length=1)
    domains: str | None = None


class FeishuCliUserAuthCompleteRequest(BaseModel):
    company_id: UUID
    open_id: str = Field(min_length=1)
    device_code: str = Field(min_length=1)
