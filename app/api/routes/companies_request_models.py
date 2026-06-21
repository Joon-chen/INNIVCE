from pydantic import BaseModel, Field


class QuickFeishuAppSetup(BaseModel):
    name: str = "飞书应用"
    app_id: str
    app_secret: str
    verification_token: str | None = None
    encrypt_key: str | None = None
    settings: dict = Field(default_factory=dict)


class QuickCompanySetupRequest(BaseModel):
    company_name: str
    company_code: str
    metadata_json: dict = Field(default_factory=dict)
    feishu_app: QuickFeishuAppSetup | None = None
    feishu_mailbox_id: str | None = None
    feishu_mail_folder_id: str = "INBOX"
    bot_admin_open_id: str | None = None
    bot_admin_name: str | None = None
