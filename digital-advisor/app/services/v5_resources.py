from typing import Any


V5_RESOURCE_TYPES = {
    "chat",
    "wiki",
    "doc",
    "bitable",
    "approval",
    "calendar",
    "mailbox",
    "meeting",
    "bot",
    "web",
}

V5_EXTENDED_RESOURCE_TYPES = {
    "directory",
    "task",
    "capability",
    "memory",
}

LEGACY_TO_V5_RESOURCE_TYPE = {
    "approval_code": "approval",
    "mail_folder": "mailbox",
    "drive_file": "doc",
    "wiki_space": "wiki",
    "bitable_app": "bitable",
    "bitable_table": "bitable",
    "external_web": "web",
    "web_page": "web",
    "website": "web",
    "url": "web",
    "memory_fact": "memory",
    "derived_memory": "memory",
}

CAPABILITY_TO_V5_RESOURCE_TYPE = {
    "feishu:contacts": "directory",
    "feishu:calendar": "calendar",
    "feishu:meetings": "meeting",
    "feishu:tasks": "task",
}


def normalize_v5_resource_type(resource_type: str | None, resource_id: str | None = None) -> str:
    normalized = (resource_type or "").strip().lower()
    external_id = (resource_id or "").strip().lower()
    if normalized == "capability":
        return CAPABILITY_TO_V5_RESOURCE_TYPE.get(external_id, "capability")
    return LEGACY_TO_V5_RESOURCE_TYPE.get(normalized, normalized)


def is_supported_v5_resource_type(resource_type: str) -> bool:
    normalized = resource_type.strip().lower()
    return normalized in V5_RESOURCE_TYPES or normalized in V5_EXTENDED_RESOURCE_TYPES


def resource_to_v5_payload(resource: Any) -> dict[str, Any]:
    resource_type = normalize_v5_resource_type(resource.resource_type, resource.resource_id)
    from app.services.data.resource_sources import EXTERNAL_WEB, data_type_for_resource, storage_for_data_type
    from app.services.v5_sync_strategy import data_layer_for_resource
    data_type = data_type_for_resource(resource_type)
    config_json = getattr(resource, "config_json", None) or {}
    source_type = EXTERNAL_WEB if _is_external_web_resource(resource, resource_type, config_json) else None

    return {
        "id": str(resource.id),
        "company_id": str(resource.company_id),
        "platform": resource.platform,
        "resource_type": resource_type,
        "data_type": data_type,
        "storage_layer": storage_for_data_type(data_type, source_type=source_type),
        "data_layer": data_layer_for_resource(resource.resource_type, resource.resource_id),
        "legacy_resource_type": resource.resource_type,
        "resource_name": resource.resource_name,
        "resource_id": resource.resource_id,
        "resource_sub_id": resource.resource_sub_id,
        "legacy_resource_id": str(resource.legacy_feishu_resource_id) if getattr(resource, "legacy_feishu_resource_id", None) else None,
        "sync_mode": resource.sync_mode,
        "permission_level": resource.permission_level,
        "data_classification": getattr(resource, "data_classification", "company"),
        "business_domain": getattr(resource, "business_domain", "general"),
        "enabled": resource.enabled,
        "last_sync_at": resource.last_sync_at.isoformat() if resource.last_sync_at else None,
        "app_config_id": str(resource.app_config_id) if resource.app_config_id else None,
        "config_json": config_json,
    }


def _is_external_web_resource(resource: Any, resource_type: str, config_json: dict[str, Any]) -> bool:
    platform = str(getattr(resource, "platform", "") or "").strip().lower()
    configured_source = str(config_json.get("source_type") or config_json.get("source") or "").strip().lower()
    return resource_type == "web" or platform in {"web", "external_web"} or configured_source in {"web", "external_web"}
