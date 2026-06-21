from typing import Any

from pydantic import BaseModel, Field


class CockpitItem(BaseModel):
    id: str | None = None
    title: str
    subtitle: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    attention_label: str | None = None
    owner: str | None = None
    due_at: str | None = None
    occurred_at: str | None = None
    source: str | None = None
    event_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CockpitModuleResult(BaseModel):
    key: str
    name: str
    description: str
    count: int = 0
    status: str = "ok"
    summary: str
    items: list[CockpitItem] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class CockpitOverview(BaseModel):
    company_id: str | None
    modules: list[CockpitModuleResult]
    metrics: dict[str, Any] = Field(default_factory=dict)
