from datetime import datetime

from pydantic import BaseModel, Field


class FeishuApprovalPendingRequest(BaseModel):
    open_id: str
    limit: int = Field(default=8, ge=1, le=50)


class FeishuChatSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=20, ge=1, le=100)
    max_pages: int = Field(default=2, ge=1, le=10)


class FeishuListMessagesRequest(BaseModel):
    chat_id: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    page_size: int = Field(default=20, ge=1, le=50)
    page_token: str | None = None


class FeishuContactDepartmentsRequest(BaseModel):
    department_id: str = "0"
    department_id_type: str = "department_id"
    page_size: int = Field(default=50, ge=1, le=50)
    page_token: str | None = None


class FeishuContactUsersRequest(BaseModel):
    department_id: str = "0"
    department_id_type: str = "department_id"
    user_id_type: str = "open_id"
    page_size: int = Field(default=50, ge=1, le=50)
    page_token: str | None = None


class FeishuContactSnapshotRequest(BaseModel):
    root_department_id: str = "0"
    max_departments: int = Field(default=100, ge=1, le=500)
    max_users: int = Field(default=500, ge=1, le=5000)


class FeishuCalendarEventsRequest(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
    page_size: int = Field(default=50, ge=50, le=100)
    page_token: str | None = None


class FeishuDocumentContentRequest(BaseModel):
    document_id: str
    document_type: str = "docx"


class FeishuDriveFilesRequest(BaseModel):
    page_size: int = Field(default=50, ge=1, le=100)
    page_token: str | None = None
    folder_token: str | None = None


class FeishuWikiSpacesRequest(BaseModel):
    page_size: int = Field(default=20, ge=1, le=50)
    page_token: str | None = None


class FeishuWikiNodesRequest(BaseModel):
    space_id: str
    parent_node_token: str | None = None
    page_size: int = Field(default=50, ge=1, le=50)
    page_token: str | None = None


class FeishuBitableTablesRequest(BaseModel):
    app_token: str
    page_size: int = Field(default=100, ge=1, le=100)
    page_token: str | None = None


class FeishuBitableRecordsRequest(BaseModel):
    app_token: str
    table_id: str
    page_size: int = Field(default=100, ge=1, le=500)
    page_token: str | None = None
    view_id: str | None = None
    field_names: list[str] = Field(default_factory=list)


class FeishuTasksRequest(BaseModel):
    page_size: int = Field(default=50, ge=1, le=100)
    page_token: str | None = None


class FeishuMailMessagesRequest(BaseModel):
    user_mailbox_id: str
    folder_id: str = "INBOX"
    page_size: int = Field(default=20, ge=1, le=20)
    page_token: str | None = None


class FeishuMailFoldersRequest(BaseModel):
    user_mailbox_id: str


class FeishuMailMessageDetailRequest(BaseModel):
    user_mailbox_id: str
    message_id: str
    format: str = "plain_text_full"


class FeishuMeetingsRequest(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
    meeting_status: int = 2
    page_size: int = Field(default=20, ge=1, le=30)
    page_token: str | None = None
    user_id_type: str = "open_id"
