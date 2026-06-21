from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ResourceSyncRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    max_pages: int = Field(default=3, ge=1, le=20)
    extract_items: bool = False
    start_time: datetime | None = None
    end_time: datetime | None = None
    large_document_mode: str = "index_only"
    bitable_mode: str = "master_data_index"
    memory_mode: str = "stable_facts_only"
    vector_mode: str = "summaries_and_hot_knowledge"


class ResourceBatchSyncRequest(ResourceSyncRequest):
    company_id: UUID
    resource_type: str | None = None
    statuses: list[str] = Field(default_factory=lambda: ["never_synced", "stale"])
    limit_resources: int = Field(default=20, ge=1, le=100)


class ResourceAccessDecisionRequest(BaseModel):
    decision: str
    note: str | None = None


class ResourceSyncPolicyUpdate(BaseModel):
    company_id: UUID
    enabled: bool = False
    interval_seconds: int = Field(default=900, ge=60, le=86400)
    limit_resources: int = Field(default=10, ge=1, le=100)
    event_limit: int = Field(default=20, ge=1, le=200)
    max_pages: int = Field(default=2, ge=1, le=20)
    resource_types: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    extract_items: bool = True
    large_document_mode: str = "index_only"
    bitable_mode: str = "master_data_index"
    memory_mode: str = "stable_facts_only"
    vector_mode: str = "summaries_and_hot_knowledge"
