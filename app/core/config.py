from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_secret_key: str = "change-me"
    admin_api_token: str | None = None
    api_base_url: str = "http://127.0.0.1:8000"

    database_url: str = "postgresql+psycopg://advisor:advisor@localhost:5432/advisor"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_provider: str = "openai"
    local_embedding_size: int = Field(default=384, ge=64, le=2048)
    openai_use_for_reports: bool = False
    openai_use_for_extraction: bool = False
    openai_use_for_bot: bool = False
    ai_provider: str = "openai"
    local_llm_base_url: str = "http://host.docker.internal:11434/v1"
    local_llm_api_key: str = "ollama"
    local_llm_model: str = "qwen2.5:14b"
    local_reasoning_model: str = "deepseek-r1:14b"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_use_for_reports: bool = False
    deepseek_use_for_extraction: bool = False
    deepseek_use_for_bot_analysis: bool = True
    bot_llm_semantics_enabled: bool = True
    bot_llm_conversation_enabled: bool = True
    bot_llm_answer_rewrite_enabled: bool = True
    bot_llm_answer_rewrite_max_chars: int = Field(default=2600, ge=200, le=6000)
    approval_llm_advice_enabled: bool = True

    feishu_base_url: str = "https://open.feishu.cn"
    feishu_event_token: str | None = None
    feishu_oauth_redirect_uri: str = "http://127.0.0.1:8000/api/feishu/oauth/callback"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "work_events"
    qdrant_enabled: bool = True
    qdrant_score_threshold: float = Field(default=0.25, ge=0.0, le=1.0)

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket: str = "advisor-attachments"

    gmail_client_id: str | None = None
    gmail_client_secret: str | None = None
    gmail_redirect_uri: str = "http://localhost:8000/api/mail/gmail/oauth/callback"

    ms_graph_client_id: str | None = None
    ms_graph_client_secret: str | None = None
    ms_graph_tenant_id: str = "common"
    ms_graph_redirect_uri: str = "http://localhost:8000/api/mail/graph/oauth/callback"

    request_timeout_seconds: float = Field(default=20.0, ge=1.0)
    ocr_enabled: bool = True
    ocr_languages: str = "chi_sim+eng"
    ocr_max_pdf_pages: int = Field(default=3, ge=1, le=10)

    auto_imap_sync_enabled: bool = False
    auto_imap_account_ids: str = ""
    auto_imap_folder: str = "INBOX"
    auto_imap_limit: int = Field(default=50, ge=1, le=200)
    auto_imap_interval_seconds: int = Field(default=1800, ge=60)

    auto_daily_report_enabled: bool = False
    auto_daily_report_company_ids: str = ""
    auto_daily_report_hour: int = Field(default=18, ge=0, le=23)
    auto_daily_report_minute: int = Field(default=30, ge=0, le=59)
    auto_daily_report_push_feishu: bool = False
    auto_feishu_sync_enabled: bool = False
    auto_feishu_app_config_ids: str = ""
    auto_feishu_sync_kinds: str = "mail,chats,contacts,calendar,tasks,meetings,drive"
    auto_feishu_sync_interval_seconds: int = Field(default=1800, ge=60)
    auto_feishu_sync_limit: int = Field(default=50, ge=1, le=200)
    auto_feishu_mail_user_mailbox_id: str | None = None
    auto_feishu_mail_folder_id: str = "INBOX"

    auto_v5_resource_sync_enabled: bool = True
    auto_v5_resource_sync_interval_seconds: int = Field(default=900, ge=60)
    auto_v5_resource_sync_limit_resources: int = Field(default=10, ge=1, le=100)
    auto_v5_resource_sync_event_limit: int = Field(default=20, ge=1, le=200)
    auto_v5_resource_sync_max_pages: int = Field(default=2, ge=1, le=20)
    auto_v5_resource_sync_resource_types: str = "approval,chat,calendar,meeting,task,directory"
    auto_v5_resource_sync_statuses: str = "never_synced,stale"

    feishu_default_app_config_id: str | None = None
    feishu_default_receive_id_type: str = "open_id"
    feishu_default_receive_id: str | None = None
    feishu_portal_app_id: str | None = None
    feishu_ws_enabled: bool = False
    feishu_bot_ai_mode_enabled: bool = False
    feishu_bot_runtime_v5_enabled: bool = False
    feishu_bot_context_events: int = Field(default=80, ge=5, le=300)
    feishu_bot_model_context_events: int = Field(default=18, ge=3, le=80)
    feishu_bot_admin_open_ids: str = ""
    feishu_sync_contacts_to_bot_users: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
