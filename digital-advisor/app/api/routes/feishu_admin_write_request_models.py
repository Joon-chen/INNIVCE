from typing import Any

from pydantic import BaseModel, Field


class FeishuSendMessageRequest(BaseModel):
    receive_id_type: str = "open_id"
    receive_id: str
    msg_type: str = "text"
    content: dict[str, Any]
    dry_run: bool = True
    confirmed: bool = False
    confirmation_token: str | None = None
    idempotency_key: str | None = None
    actor_open_id: str | None = None


class FeishuApprovalActionRequest(BaseModel):
    open_id: str
    approval_code: str
    instance_code: str
    task_id: str
    action: str = Field(pattern="^(approve|reject)$")
    comment: str = "由数字参谋管理后台二次确认后提交。"
    dry_run: bool = True
    confirmed: bool = False
    confirmation_token: str | None = None


class FeishuCreateChatRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    user_id_list: list[str] = Field(default_factory=list)
    bot_id_list: list[str] = Field(default_factory=list)
    include_current_bot: bool = True
    user_id_type: str = "open_id"
    chat_mode: str = "group"
    chat_type: str = "private"
    dry_run: bool = True
    confirmed: bool = False
    confirmation_token: str | None = None
    actor_open_id: str | None = None


class FeishuAutoJoinPublicChatsRequest(BaseModel):
    query: str | None = None
    chat_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)
    max_pages: int = Field(default=2, ge=1, le=10)
    dry_run: bool = True
    confirmed: bool = False
    confirmation_token: str | None = None
    actor_open_id: str | None = None
