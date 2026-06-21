from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FeishuAppCreate(BaseModel):
    company_id: UUID
    name: str
    app_id: str
    app_secret: str
    verification_token: str | None = None
    encrypt_key: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class FeishuAppOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    name: str
    app_id: str
    is_active: bool


class FeishuOAuthExchangeRequest(BaseModel):
    code: str
