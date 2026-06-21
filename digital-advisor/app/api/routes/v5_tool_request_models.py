from typing import Any

from pydantic import BaseModel, Field

from app.services.tools.base import ToolProvider


class ToolConfigUpdate(BaseModel):
    enabled: bool | None = None
    provider: ToolProvider | None = None
    config_json: dict[str, Any] | None = None


class ToolBatchUpdateRequest(BaseModel):
    all_tools: bool = False
    tool_names: list[str] = Field(default_factory=list)
    include_prefixes: list[str] = Field(default_factory=list)
    include_providers: list[ToolProvider] = Field(default_factory=list)
    supports_write: bool | None = None
    enabled: bool | None = None
    provider: ToolProvider | None = None
    config_json: dict[str, Any] | None = None
    dry_run: bool = True


class ToolExecutionRequest(BaseModel):
    question: str | None = None
    normalized_command: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    chat_id: str | None = None
    actor_role: str = "admin"
    actor_access_scope: str = "company"
    actor_domains: list[str] = Field(default_factory=lambda: ["approval", "bitable", "task", "calendar", "im"])
    actor_display_name: str | None = "admin_console"
    actor_open_id: str | None = None
    actor_email: str | None = None
