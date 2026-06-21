from typing import Any


APP_IDENTITY = "app_identity"
USER_IDENTITY = "user_identity"
LOCAL_IMPORT = "local_import"
EXTERNAL_CONNECTOR = "external_connector"
UNKNOWN_IDENTITY = "unknown"

FEISHU_APP_IDENTITY = "feishu_app_identity"
FEISHU_USER_IDENTITY = "feishu_user_identity"
EXTERNAL_MAIL_ACCOUNT = "external_mail_account"
PERSONAL_DINGTALK_ACCOUNT = "personal_dingtalk_account"
EXTERNAL_WEB = "external_web"

OPERATIONAL_DATA = "operational_data"
MEMORY_DATA = "memory_data"
KNOWLEDGE_DATA = "knowledge_data"
UNKNOWN_DATA = "unknown_data"

SOURCE_TYPE_ALIASES = {
    "feishu_app_identity": FEISHU_APP_IDENTITY,
    "feishu_app": FEISHU_APP_IDENTITY,
    "lark_app": FEISHU_APP_IDENTITY,
    "tenant_app": FEISHU_APP_IDENTITY,
    "app_identity": FEISHU_APP_IDENTITY,
    "feishu_user_identity": FEISHU_USER_IDENTITY,
    "feishu_user": FEISHU_USER_IDENTITY,
    "lark_user": FEISHU_USER_IDENTITY,
    "user_identity": FEISHU_USER_IDENTITY,
    "external_mail_account": EXTERNAL_MAIL_ACCOUNT,
    "mail_account": EXTERNAL_MAIL_ACCOUNT,
    "gmail": EXTERNAL_MAIL_ACCOUNT,
    "graph": EXTERNAL_MAIL_ACCOUNT,
    "imap": EXTERNAL_MAIL_ACCOUNT,
    "personal_dingtalk_account": PERSONAL_DINGTALK_ACCOUNT,
    "dingtalk": PERSONAL_DINGTALK_ACCOUNT,
    "dingtalk_user": PERSONAL_DINGTALK_ACCOUNT,
    "manual_import": LOCAL_IMPORT,
    "csv_import": LOCAL_IMPORT,
    "local_import": LOCAL_IMPORT,
    "external_web": EXTERNAL_WEB,
    "web": EXTERNAL_WEB,
    "external_connector": EXTERNAL_CONNECTOR,
}

SOURCE_IDENTITY_ALIASES = {
    "app_identity": APP_IDENTITY,
    "feishu_app_identity": APP_IDENTITY,
    "feishu_app": APP_IDENTITY,
    "lark_app": APP_IDENTITY,
    "tenant_app": APP_IDENTITY,
    "user_identity": USER_IDENTITY,
    "feishu_user_identity": USER_IDENTITY,
    "feishu_user": USER_IDENTITY,
    "lark_user": USER_IDENTITY,
    "external_mail_account": USER_IDENTITY,
    "mail_account": USER_IDENTITY,
    "gmail": USER_IDENTITY,
    "graph": USER_IDENTITY,
    "imap": USER_IDENTITY,
    "local_import": LOCAL_IMPORT,
    "manual_import": LOCAL_IMPORT,
    "csv_import": LOCAL_IMPORT,
    "external_connector": EXTERNAL_CONNECTOR,
    "personal_dingtalk_account": EXTERNAL_CONNECTOR,
    "dingtalk": EXTERNAL_CONNECTOR,
    "external_web": EXTERNAL_CONNECTOR,
    "web": EXTERNAL_CONNECTOR,
}

IDENTITY_PRIORITIES = {
    APP_IDENTITY: 100,
    USER_IDENTITY: 80,
    EXTERNAL_CONNECTOR: 60,
    LOCAL_IMPORT: 20,
    UNKNOWN_IDENTITY: 0,
}


DATA_SOURCE_GROUPS = {
    FEISHU_APP_IDENTITY: "feishu_enterprise_app",
    FEISHU_USER_IDENTITY: "feishu_personal_user",
    EXTERNAL_MAIL_ACCOUNT: "external_mail",
    PERSONAL_DINGTALK_ACCOUNT: "personal_dingtalk",
    EXTERNAL_WEB: "knowledge_external_web",
    LOCAL_IMPORT: "local_import",
    EXTERNAL_CONNECTOR: "external_connector",
}

DATA_TYPE_STORAGE = {
    OPERATIONAL_DATA: "postgresql",
    MEMORY_DATA: "memory_facts",
    KNOWLEDGE_DATA: "document_store_vector_db",
    UNKNOWN_DATA: "manual_review",
}

OPERATIONAL_RESOURCE_TYPES = {
    "approval",
    "approval_code",
    "bitable",
    "bitable_app",
    "bitable_table",
    "calendar",
    "chat",
    "directory",
    "mailbox",
    "mail_folder",
    "meeting",
    "task",
}

KNOWLEDGE_RESOURCE_TYPES = {"doc", "drive_file", "drive_folder", "wiki", "wiki_space"}
EXTERNAL_WEB_RESOURCE_TYPES = {"web", "web_page", "website", "url", "external_web"}


def normalize_source_type(source_type: str | None) -> str:
    normalized = (source_type or "").strip().lower()
    return SOURCE_TYPE_ALIASES.get(normalized, normalized or UNKNOWN_IDENTITY)


def source_identity_type(source_type: str | None) -> str:
    normalized = normalize_source_type(source_type)
    return SOURCE_IDENTITY_ALIASES.get(normalized, SOURCE_IDENTITY_ALIASES.get((source_type or "").strip().lower(), UNKNOWN_IDENTITY))


def source_priority(source_type: str | None) -> int:
    return IDENTITY_PRIORITIES[source_identity_type(source_type)]


def source_priority_reason(source_type: str | None) -> str:
    identity_type = source_identity_type(source_type)
    if identity_type == APP_IDENTITY:
        return "enterprise_app_identity_preferred"
    if identity_type == USER_IDENTITY:
        return "user_identity_supplements_app_identity"
    if identity_type == EXTERNAL_CONNECTOR:
        return "external_connector_after_native_identity"
    if identity_type == LOCAL_IMPORT:
        return "local_import_fallback"
    return "unknown_source_lowest_priority"


def data_source_group(source_type: str | None) -> str:
    return DATA_SOURCE_GROUPS.get(normalize_source_type(source_type), "unknown")


def data_type_for_resource(resource_type: str | None) -> str:
    normalized = (resource_type or "").strip().lower()
    if normalized in OPERATIONAL_RESOURCE_TYPES:
        return OPERATIONAL_DATA
    if normalized in KNOWLEDGE_RESOURCE_TYPES or normalized in EXTERNAL_WEB_RESOURCE_TYPES:
        return KNOWLEDGE_DATA
    if normalized in {"memory", "memory_fact", "derived_memory"}:
        return MEMORY_DATA
    return UNKNOWN_DATA


def storage_for_data_type(data_type: str | None, *, source_type: str | None = None) -> str:
    if (data_type or "").strip().lower() == KNOWLEDGE_DATA and normalize_source_type(source_type) == EXTERNAL_WEB:
        return "web"
    return DATA_TYPE_STORAGE.get((data_type or "").strip().lower(), DATA_TYPE_STORAGE[UNKNOWN_DATA])


def preferred_source(sources: list[Any]) -> Any | None:
    syncable = [source for source in sources if getattr(source, "can_sync", True)]
    ordered = sorted(
        syncable,
        key=lambda source: (
            getattr(source, "priority", None)
            if getattr(source, "priority", None) is not None
            else source_priority(getattr(source, "source_type", None)),
            getattr(source, "last_seen_at", None),
        ),
        reverse=True,
    )
    return ordered[0] if ordered else None


def source_policy_payload(source: Any) -> dict[str, Any]:
    source_type = getattr(source, "source_type", None)
    normalized_source_type = normalize_source_type(source_type)
    return {
        "source_type": source_type,
        "normalized_source_type": normalized_source_type,
        "data_source": data_source_group(normalized_source_type),
        "identity_type": source_identity_type(source_type),
        "priority": getattr(source, "priority", None)
        if getattr(source, "priority", None) is not None
        else source_priority(source_type),
        "priority_reason": source_priority_reason(source_type),
        "can_sync": getattr(source, "can_sync", True),
    }
