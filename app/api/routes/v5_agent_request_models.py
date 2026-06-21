from uuid import UUID

from pydantic import BaseModel, Field


class AgentTracePreviewRequest(BaseModel):
    company_id: UUID
    question: str = Field(min_length=1)
    normalized_command: str | None = None
    chat_id: str | None = None
    actor_role: str = "owner"
    actor_access_scope: str = "company"
    actor_domains: list[str] = Field(default_factory=lambda: ["finance", "sales", "rd", "delivery", "hr", "admin"])
    actor_display_name: str | None = None
    actor_open_id: str | None = None
    actor_email: str | None = None


class AgentSettingsUpdate(BaseModel):
    enabled: bool | None = None
    default_model: str | None = None
    planner_enabled: bool | None = None
    max_tool_calls: int | None = Field(default=None, ge=1, le=20)
    max_planner_steps: int | None = Field(default=None, ge=1, le=10)
    memory_mode: str | None = None
    answer_style: str | None = None
    trace_enabled: bool | None = None
    allow_write_tools: bool | None = None
    require_write_confirmation: bool | None = None
