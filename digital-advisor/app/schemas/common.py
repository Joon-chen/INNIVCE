from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CompanyCreate(BaseModel):
    name: str
    code: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str
    status: str = "active"
    created_at: datetime


class AccountCreate(BaseModel):
    company_id: UUID
    provider: str
    account_type: str | None = None
    display_name: str
    external_account_id: str | None = None
    email_address: EmailStr | None = None
    credentials: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    provider: str
    account_type: str
    display_name: str
    external_account_id: str | None = None
    email_address: EmailStr | None = None
    is_active: bool


class WorkEventCreate(BaseModel):
    company_id: UUID
    account_id: UUID | None = None
    resource_id: UUID | None = None
    source: str
    source_type: str = "unknown"
    source_account_id: str | None = None
    visibility_scope: str = "company"
    allowed_user_ids: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    allowed_departments: list[str] = Field(default_factory=list)
    data_classification: str = "company"
    business_domain: str = "general"
    event_type: str
    external_id: str | None = None
    thread_id: str | None = None
    title: str | None = None
    content_text: str | None = None
    occurred_at: datetime | None = None
    actors: list[dict[str, Any]] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_json: dict[str, Any] | None = None
    importance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    sensitivity: str = "normal"


class WorkEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    account_id: UUID | None
    resource_id: UUID | None = None
    source: str
    source_type: str = "unknown"
    source_account_id: str | None = None
    visibility_scope: str = "company"
    allowed_user_ids: list
    allowed_roles: list
    allowed_departments: list
    data_classification: str = "company"
    business_domain: str = "general"
    event_type: str
    external_id: str | None
    thread_id: str | None
    title: str | None
    content_text: str | None
    occurred_at: datetime
    actors: list
    labels: list
    importance_score: float = 0.0
    sensitivity: str
    vector_status: str
