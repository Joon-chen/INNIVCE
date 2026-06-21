import json
from datetime import UTC, datetime
from typing import Any
from types import SimpleNamespace
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Account, FeishuAppConfig, Resource, ResourceSource, WorkEvent
from app.services.ai.extraction import extract_items_for_event
from app.services.data.resource_sources import FEISHU_APP_IDENTITY, FEISHU_USER_IDENTITY, normalize_source_type
from app.services.feishu.client import FeishuClient
from app.services.feishu.resources import _user_access_token
from app.services.resource_sources import preferred_sync_source, resource_source_payload
from app.services.resource_sync_runs import finish_resource_sync_run, start_resource_sync_run
from app.services.feishu import sync_feishu_information
from app.schemas.common import WorkEventCreate
from app.services.work_events import upsert_work_event
from app.services.v5_resource_governance import set_resource_access_decision
from app.services.v5_resources import normalize_v5_resource_type, resource_to_v5_payload
from app.services.v5_sync_decisions import ACCESS_BLOCKED_STATUSES, sync_decision_for_resource


def clear_v5_resource_access_block(db: Session, *, resource_id: UUID) -> dict[str, Any]:
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    removed = _clear_resource_access_block(resource)
    db.commit()
    return {
        "resource_id": str(resource.id),
        "cleared": removed,
        "resource": resource_to_v5_payload(resource),
    }


async def retry_v5_resource_access_block(
    db: Session,
    *,
    resource_id: UUID,
    limit: int = 20,
    max_pages: int = 3,
    extract_items: bool = False,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    large_document_mode: str = "index_only",
    bitable_mode: str = "master_data_index",
    memory_mode: str = "stable_facts_only",
    vector_mode: str = "summaries_and_hot_knowledge",
) -> dict[str, Any]:
    result = await sync_v5_resource(
        db,
        resource_id=resource_id,
        limit=limit,
        max_pages=max_pages,
        extract_items=extract_items,
        start_time=start_time,
        end_time=end_time,
        large_document_mode=large_document_mode,
        bitable_mode=bitable_mode,
        memory_mode=memory_mode,
        vector_mode=vector_mode,
        retry_access_blocked=True,
    )
    resource = db.get(Resource, resource_id)
    return {
        **result,
        "resource": resource_to_v5_payload(resource) if resource else None,
    }


def update_v5_resource_access_decision(
    db: Session,
    *,
    resource_id: UUID,
    decision: str,
    note: str | None = None,
) -> dict[str, Any]:
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    try:
        governance = set_resource_access_decision(resource, decision=decision, note=note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return {
        "resource_id": str(resource.id),
        "governance": governance,
        "resource": resource_to_v5_payload(resource),
    }


async def sync_v5_resource(
    db: Session,
    *,
    resource_id: UUID,
    limit: int = 20,
    max_pages: int = 3,
    extract_items: bool = False,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    large_document_mode: str = "index_only",
    bitable_mode: str = "master_data_index",
    memory_mode: str = "stable_facts_only",
    vector_mode: str = "summaries_and_hot_knowledge",
    retry_access_blocked: bool = False,
) -> dict[str, Any]:
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    decision_resource = _decision_resource(resource, retry_access_blocked=retry_access_blocked)
    decision = sync_decision_for_resource(
        decision_resource,
        _request_sync_policy(
            large_document_mode=large_document_mode,
            bitable_mode=bitable_mode,
            memory_mode=memory_mode,
            vector_mode=vector_mode,
        ),
    )
    sync_action = _sync_action_from_decision(decision)
    access_block_cleared = False
    run = start_resource_sync_run(
        db,
        company_id=resource.company_id,
        resource_id=resource.id,
        sync_action=decision["sync_action"],
        summary={
            "data_layer": decision["data_layer"],
            "resource_type": decision["resource_type"],
            "query_path": decision["query_path"],
            "reason": decision["reason"],
            "decision_status": decision["status"],
        },
    )
    if not decision["executable"]:
        finish_resource_sync_run(
            run,
            status="skipped",
            items_skipped=1,
            error_message=decision["reason"],
            summary=decision,
        )
        db.commit()
        return {
            "available": False,
            "resource_id": str(resource.id),
            "resource_sync_run_id": str(run.id),
            "resource_type": decision["resource_type"],
            "data_layer": decision["data_layer"],
            "sync_action": decision["sync_action"],
            "query_path": decision["query_path"],
            "decision_status": decision["status"],
            "saved_count": 0,
            "error": decision["reason"],
        }
    sync_source = preferred_sync_source(db, resource)
    app_config_id = _app_config_id_for_sync(resource, sync_source)
    if not app_config_id:
        finish_resource_sync_run(
            run,
            status="failed",
            error_message="Resource has no Feishu app_config_id.",
        )
        db.commit()
        return {
            "available": False,
            "resource_id": str(resource.id),
            "resource_sync_run_id": str(run.id),
            "sync_action": decision["sync_action"],
            "query_path": decision["query_path"],
            "saved_count": 0,
            "error": "Resource has no Feishu app_config_id.",
        }
    app_config = db.get(FeishuAppConfig, app_config_id)
    if app_config is None:
        finish_resource_sync_run(
            run,
            status="failed",
            error_message="Resource Feishu app configuration was not found.",
        )
        db.commit()
        return {
            "available": False,
            "resource_id": str(resource.id),
            "resource_sync_run_id": str(run.id),
            "sync_action": decision["sync_action"],
            "query_path": decision["query_path"],
            "saved_count": 0,
            "error": "Resource Feishu app configuration was not found.",
        }

    if sync_action["action"] == "document_index_only":
        event = _index_resource_metadata_event(db, resource=resource, sync_action=sync_action)
        _apply_source_metadata_to_work_events(db, resource=resource, source=sync_source, work_event_ids=[str(event.id)])
        resource.last_sync_at = datetime.now(UTC)
        access_block_cleared = _clear_resource_access_block(resource)
        finish_resource_sync_run(
            run,
            status="success",
            items_seen=1,
            items_indexed=1,
            summary={
                "data_layer": sync_action["data_layer"],
                "resource_type": sync_action["resource_type"],
                "query_path": sync_action["query_path"],
                "work_event_ids": [str(event.id)],
                "mode": "index_only",
                "source": resource_source_payload(sync_source),
            },
        )
        db.commit()
        return {
            "available": True,
            "resource_id": str(resource.id),
            "resource_sync_run_id": str(run.id),
            "resource_type": normalize_v5_resource_type(resource.resource_type, resource.resource_id),
            "legacy_resource_type": resource.resource_type,
            "resource_name": resource.resource_name,
            "data_layer": sync_action["data_layer"],
            "sync_action": sync_action["action"],
            "query_path": sync_action["query_path"],
            "saved_count": 1,
            "access_block_cleared": access_block_cleared,
            "work_event_ids": [str(event.id)],
            "results": {"index": {"available": True, "saved_count": 1, "work_event_ids": [str(event.id)]}},
        }

    if sync_source and normalize_source_type(sync_source.source_type) == FEISHU_USER_IDENTITY:
        result = await _sync_user_authorized_resource(
            db,
            app_config=app_config,
            resource=resource,
            source=sync_source,
            limit=limit,
            max_pages=max_pages,
            extract_items=_extract_items_for_sync(extract_items=extract_items, sync_action=sync_action),
        )
        work_event_ids = _work_event_ids_from_result(result)
        items_seen = _items_seen_from_result(result, minimum=len(work_event_ids))
        available = all(item.get("available", True) for item in result.values() if isinstance(item, dict))
        error_message = _error_message_from_result(result)
        _bind_work_events_to_resource(db, resource=resource, work_event_ids=work_event_ids)
        _apply_source_metadata_to_work_events(db, resource=resource, source=sync_source, work_event_ids=work_event_ids)
        if available and not error_message:
            access_block_cleared = _clear_resource_access_block(resource)
            resource.last_sync_at = datetime.now(UTC)
        finish_resource_sync_run(
            run,
            status="success" if available else "partial",
            items_seen=items_seen,
            items_indexed=len(work_event_ids),
            items_skipped=max(items_seen - len(work_event_ids), 0),
            error_message=error_message,
            summary={
                "data_layer": sync_action["data_layer"],
                "resource_type": sync_action["resource_type"],
                "query_path": sync_action["query_path"],
                "work_event_ids": work_event_ids,
                "source": resource_source_payload(sync_source),
                "mode": "feishu_user_token",
            },
        )
        db.commit()
        return {
            "available": available,
            "resource_id": str(resource.id),
            "resource_sync_run_id": str(run.id),
            "resource_type": normalize_v5_resource_type(resource.resource_type, resource.resource_id),
            "legacy_resource_type": resource.resource_type,
            "resource_name": resource.resource_name,
            "data_layer": sync_action["data_layer"],
            "sync_action": sync_action["action"],
            "query_path": sync_action["query_path"],
            "saved_count": len(work_event_ids),
            "access_block_cleared": access_block_cleared,
            "work_event_ids": work_event_ids,
            "results": result,
            "source": resource_source_payload(sync_source),
        }

    sync_args = _sync_args_for_resource(resource)
    if sync_args is None:
        finish_resource_sync_run(
            run,
            status="skipped",
            items_skipped=1,
            error_message="This resource type is not yet supported by V5 resource sync.",
        )
        db.commit()
        return {
            "available": False,
            "resource_id": str(resource.id),
            "resource_sync_run_id": str(run.id),
            "resource_type": normalize_v5_resource_type(resource.resource_type, resource.resource_id),
            "data_layer": sync_action["data_layer"],
            "sync_action": sync_action["action"],
            "query_path": sync_action["query_path"],
            "saved_count": 0,
            "error": "This resource type is not yet supported by V5 resource sync.",
        }

    try:
        result = await sync_feishu_information(
            db,
            app_config,
            limit=limit,
            max_pages=max_pages,
            extract_items=_extract_items_for_sync(extract_items=extract_items, sync_action=sync_action),
            start_time=start_time,
            end_time=end_time,
            sync_context=_sync_context_for_resource(resource=resource, sync_action=sync_action),
            **sync_args,
        )
    except Exception as exc:
        finish_resource_sync_run(run, status="failed", error_message=str(exc)[:1000])
        db.commit()
        raise
    work_event_ids = _work_event_ids_from_result(result)
    items_seen = _items_seen_from_result(result, minimum=len(work_event_ids))
    available = all(item.get("available", True) for item in result.values() if isinstance(item, dict))
    error_message = _error_message_from_result(result)
    rag_indexing = _rag_indexing_from_result(result)
    document_store = _document_store_from_result(result)
    normalized_resource_type = normalize_v5_resource_type(resource.resource_type, resource.resource_id)
    if _is_bot_not_in_chat_error(error_message) and normalized_resource_type == "chat":
        _mark_resource_access_blocked(
            resource,
            status="bot_not_in_chat",
            reason="机器人不在该群，飞书拒绝读取群消息。",
            error_message=error_message,
        )
    if _is_calendar_authorization_error(error_message) and normalized_resource_type == "calendar":
        _mark_resource_access_blocked(
            resource,
            status="requires_user_authorization",
            reason="应用身份不能直接读取该日历资源，请完成用户授权；系统会继续自动发现可同步日历。",
            error_message=error_message,
        )
    if available and not error_message:
        access_block_cleared = _clear_resource_access_block(resource)
    _bind_work_events_to_resource(db, resource=resource, work_event_ids=work_event_ids)
    _apply_source_metadata_to_work_events(db, resource=resource, source=sync_source, work_event_ids=work_event_ids)
    resource.last_sync_at = datetime.now(UTC)
    finish_resource_sync_run(
        run,
        status="success" if available else "partial",
        items_seen=items_seen,
        items_indexed=len(work_event_ids),
        items_skipped=max(items_seen - len(work_event_ids), 0),
        error_message=error_message,
        summary={
            "data_layer": sync_action["data_layer"],
            "resource_type": sync_action["resource_type"],
            "query_path": sync_action["query_path"],
            "work_event_ids": work_event_ids,
            "sync_args": sync_args,
            "access_block_cleared": access_block_cleared,
            "source": resource_source_payload(sync_source),
            "rag_indexing": rag_indexing,
            "document_store": document_store,
        },
    )
    db.commit()
    return {
        "available": available,
        "resource_id": str(resource.id),
        "resource_sync_run_id": str(run.id),
        "resource_type": normalize_v5_resource_type(resource.resource_type, resource.resource_id),
        "legacy_resource_type": resource.resource_type,
        "resource_name": resource.resource_name,
        "data_layer": sync_action["data_layer"],
        "sync_action": sync_action["action"],
        "query_path": sync_action["query_path"],
        "memory_mode": memory_mode,
        "vector_mode": vector_mode,
        "saved_count": len(work_event_ids),
        "access_block_cleared": access_block_cleared,
        "work_event_ids": work_event_ids,
        "results": result,
        "source": resource_source_payload(sync_source),
        "rag_indexing": rag_indexing,
        "document_store": document_store,
    }


def _app_config_id_for_sync(resource: Resource, source: ResourceSource | None) -> UUID | None:
    if source and normalize_source_type(source.source_type) == FEISHU_APP_IDENTITY and source.source_account_id:
        try:
            return UUID(source.source_account_id)
        except ValueError:
            return resource.app_config_id
    return resource.app_config_id


def _decision_resource(resource: Resource, *, retry_access_blocked: bool) -> Any:
    if not retry_access_blocked:
        return resource
    config = dict(resource.config_json or {})
    governance = config.get("governance") or {}
    status = str(governance.get("status") or "") if isinstance(governance, dict) else ""
    if status not in ACCESS_BLOCKED_STATUSES:
        return resource
    config.pop("governance", None)
    return SimpleNamespace(
        resource_type=resource.resource_type,
        resource_id=resource.resource_id,
        resource_sub_id=resource.resource_sub_id,
        platform=resource.platform,
        enabled=resource.enabled,
        config_json=config,
    )


def _clear_resource_access_block(resource: Resource) -> bool:
    config = dict(resource.config_json or {})
    removed = config.pop("governance", None) is not None
    if removed:
        resource.config_json = config
    return removed


def _request_sync_policy(
    *,
    large_document_mode: str,
    bitable_mode: str,
    memory_mode: str,
    vector_mode: str,
) -> dict[str, Any]:
    return {
        "large_document_mode": large_document_mode,
        "bitable_mode": bitable_mode,
        "memory_mode": memory_mode,
        "vector_mode": vector_mode,
    }


def _sync_action_from_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_layer": decision["data_layer"],
        "resource_type": decision["resource_type"],
        "action": decision["sync_action"],
        "query_path": decision["query_path"],
        "extract_items": decision["extract_items"],
        "vectorize": decision["vectorize"],
        "reason": decision["reason"],
    }


def _extract_items_for_sync(*, extract_items: bool, sync_action: dict[str, Any]) -> bool:
    if sync_action["action"] == "knowledge_vectorize":
        return True
    return extract_items and bool(sync_action.get("extract_items", True))


def _sync_context_for_resource(*, resource: Resource, sync_action: dict[str, Any]) -> dict[str, Any]:
    return {
        "resource_id": str(resource.id),
        "resource_name": resource.resource_name,
        "resource_type": normalize_v5_resource_type(resource.resource_type, resource.resource_id),
        "legacy_resource_type": resource.resource_type,
        "data_layer": sync_action["data_layer"],
        "sync_action": sync_action["action"],
        "query_path": sync_action["query_path"],
        "vectorize": sync_action["vectorize"],
    }


def _sync_args_for_resource(resource: Resource) -> dict[str, Any] | None:
    resource_type = normalize_v5_resource_type(resource.resource_type, resource.resource_id)
    if resource_type == "mailbox":
        folder_id = resource.resource_sub_id or (resource.config_json or {}).get("folder_id")
        if not folder_id:
            return None
        return {"kinds": ["mail"], "user_mailbox_id": resource.resource_id, "folder_id": folder_id}
    if resource_type == "chat":
        return {"kinds": ["messages"], "chat_id": resource.resource_id}
    if resource_type == "approval":
        return {"kinds": ["approvals"], "approval_code": resource.resource_id}
    if resource_type == "calendar":
        return {"kinds": ["calendar"]}
    if resource_type == "meeting":
        return {"kinds": ["meetings"]}
    if resource_type == "task":
        return {"kinds": ["tasks"]}
    if resource_type == "directory":
        return {"kinds": ["contacts"]}
    if resource_type == "doc":
        document_type = (resource.config_json or {}).get("document_type") or "docx"
        return {"kinds": [str(document_type)], "document_id": resource.resource_id, "document_type": str(document_type)}
    if resource_type == "bitable" and resource.resource_sub_id:
        return {"kinds": ["bitable"], "app_token": resource.resource_id, "table_id": resource.resource_sub_id}
    return None


async def _sync_user_authorized_resource(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    resource: Resource,
    source: ResourceSource,
    limit: int,
    max_pages: int,
    extract_items: bool,
) -> dict[str, Any]:
    resource_type = normalize_v5_resource_type(resource.resource_type, resource.resource_id)
    if resource_type == "bitable" and resource.resource_sub_id:
        return {
            "bitable": await _sync_user_bitable_records(
                db,
                app_config=app_config,
                resource=resource,
                source=source,
                limit=limit,
                max_pages=max_pages,
                extract_items=extract_items,
            )
        }
    return {
        resource_type: {
            "available": False,
            "saved_count": 0,
            "error": f"个人飞书授权暂不支持同步该资源类型：{resource_type}",
        }
    }


async def _sync_user_bitable_records(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    resource: Resource,
    source: ResourceSource,
    limit: int,
    max_pages: int,
    extract_items: bool,
) -> dict[str, Any]:
    account = _feishu_user_account_for_source(db, source)
    if account is None:
        return {"available": False, "saved_count": 0, "error": "用户级能力包授权不存在或已停用。"}
    client = FeishuClient(app_config)
    user_token, error = await _user_access_token(db, account=account, app_config=app_config, client=client)
    if not user_token:
        return {"available": False, "saved_count": 0, "error": error or "用户级能力包授权不可用，请由资源所有者本人重新授权。"}

    app_token = resource.resource_id
    table_id = resource.resource_sub_id
    path = f"/open-apis/bitable/v1/apps/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}/records"
    saved_ids: list[str] = []
    page_token = None
    page_count = 0
    has_more = False
    try:
        while page_count < max_pages and len(saved_ids) < limit:
            remaining = limit - len(saved_ids)
            params = {"page_size": min(max(remaining, 1), 100)}
            if page_token:
                params["page_token"] = page_token
            body = await client.api_get_user(path, user_access_token=user_token, params=params)
            page_count += 1
            data = body.get("data") or {}
            for item in _items(data, "items", "records"):
                event = upsert_work_event(
                    db,
                    _user_bitable_record_event(
                        app_config,
                        resource=resource,
                        app_token=app_token,
                        table_id=table_id,
                        item=item,
                    ),
                )
                saved_ids.append(str(event.id))
                if extract_items:
                    extract_items_for_event(db, event)
                if len(saved_ids) >= limit:
                    break
            has_more = bool(data.get("has_more"))
            page_token = data.get("page_token")
            if not has_more or not page_token:
                break
    except HTTPException as exc:
        return {"available": False, "saved_count": len(saved_ids), "work_event_ids": saved_ids, "error": _safe_error(exc.detail)}
    return {
        "available": True,
        "saved_count": len(saved_ids),
        "work_event_ids": saved_ids,
        "items_seen": len(saved_ids),
        "has_more": has_more,
        "page_token": page_token,
        "page_count": page_count,
        "mode": "master_data_index",
        "source": FEISHU_USER_IDENTITY,
    }


def _feishu_user_account_for_source(db: Session, source: ResourceSource) -> Account | None:
    if not source.source_account_id:
        return None
    try:
        account_id = UUID(str(source.source_account_id))
    except ValueError:
        return None
    account = db.get(Account, account_id)
    if account is None or account.provider != "feishu_user" or not account.is_active:
        return None
    return account


def _user_bitable_record_event(
    app_config: FeishuAppConfig,
    *,
    resource: Resource,
    app_token: str,
    table_id: str,
    item: dict[str, Any],
) -> WorkEventCreate:
    record_id = _first_present(item, "record_id", "id") or _stable_item_id(item)
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    key_fields = _compact_bitable_key_fields(fields)
    field_names = sorted(str(key) for key in fields.keys())
    title = _bitable_record_title(resource.resource_name or table_id, record_id, key_fields)
    content = {
        "resource_name": resource.resource_name,
        "record_id": record_id,
        "table_id": table_id,
        "field_names": field_names[:80],
        "key_fields": key_fields,
        "source": "feishu_user",
    }
    return WorkEventCreate(
        company_id=app_config.company_id,
        source="feishu",
        resource_id=resource.id,
        source_type=FEISHU_USER_IDENTITY,
        event_type="feishu.bitable.master_data_index",
        external_id=f"feishu-user-bitable:{app_token}:{table_id}:{record_id}",
        title=title,
        content_text=json.dumps(content, ensure_ascii=False, default=str)[:3000],
        occurred_at=_parse_item_time(item),
        labels=["feishu", "bitable", "master_data_index", FEISHU_USER_IDENTITY],
        payload={
            "kind": "bitable",
            "mode": "master_data_index",
            "app_token": app_token,
            "table_id": table_id,
            "record_id": record_id,
            "field_names": field_names[:80],
            "key_fields": key_fields,
            "source": FEISHU_USER_IDENTITY,
        },
    )


def _work_event_ids_from_result(result: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for value in result.values():
        if not isinstance(value, dict):
            continue
        for event_id in value.get("work_event_ids") or []:
            text = str(event_id)
            if text and text not in ids:
                ids.append(text)
    return ids


def _items(data: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _first_present(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _stable_item_id(item: dict[str, Any]) -> str:
    text = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    return str(uuid5(NAMESPACE_URL, text))


def _compact_bitable_key_fields(fields: dict[str, Any]) -> dict[str, str]:
    selected: dict[str, str] = {}
    preferred_terms = ("名称", "客户", "项目", "合同", "订单", "金额", "状态", "负责人", "日期", "编号", "name", "customer", "project", "amount", "status")
    for key in sorted(fields.keys(), key=str):
        key_text = str(key)
        if len(selected) >= 12:
            break
        if any(term.lower() in key_text.lower() for term in preferred_terms):
            selected[key_text] = _compact_value(fields[key])
    if selected:
        return selected
    for key in sorted(fields.keys(), key=str)[:12]:
        selected[str(key)] = _compact_value(fields[key])
    return selected


def _compact_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(_compact_value(item) for item in value[:5])[:300]
    if isinstance(value, dict):
        for key in ("text", "name", "title", "value", "email", "en_name"):
            if value.get(key) not in (None, ""):
                return str(value[key])[:300]
        return json.dumps(value, ensure_ascii=False, default=str)[:300]
    return str(value)[:300]


def _bitable_record_title(table_name: str, record_id: str, key_fields: dict[str, str]) -> str:
    if key_fields:
        preview = " / ".join(value for value in key_fields.values() if value)[:80]
        if preview:
            return f"{table_name}：{preview}"
    return f"{table_name}：{record_id}"


def _parse_item_time(item: dict[str, Any]) -> datetime:
    for key in ("updated_time", "created_time", "last_modified_time"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            text = str(value)
            timestamp = int(text)
            if timestamp > 10_000_000_000:
                timestamp = timestamp // 1000
            return datetime.fromtimestamp(timestamp, UTC)
        except (TypeError, ValueError, OSError):
            continue
    return datetime.now(UTC)


def _safe_error(value: Any) -> str:
    if isinstance(value, dict):
        message = value.get("message") or value.get("msg")
        body = value.get("body")
        if isinstance(body, dict):
            message = message or body.get("msg") or body.get("message")
        if message:
            return str(message)[:1000]
    return str(value)[:1000]


def _items_seen_from_result(result: dict[str, Any], *, minimum: int = 0) -> int:
    count = 0
    all_work_event_ids: list[str] = []
    for value in result.values():
        if not isinstance(value, dict):
            continue
        work_event_ids = value.get("work_event_ids") or []
        if work_event_ids:
            all_work_event_ids.extend(str(event_id) for event_id in work_event_ids if event_id)
        elif isinstance(value.get("items_seen"), int):
            count += value["items_seen"]
        elif isinstance(value.get("saved_count"), int):
            count += value["saved_count"]
        elif isinstance(value.get("total"), int):
            count += value["total"]
    if all_work_event_ids:
        count += len(dict.fromkeys(all_work_event_ids))
    return max(count, minimum)


def _error_message_from_result(result: dict[str, Any]) -> str | None:
    errors: list[str] = []
    for value in result.values():
        if not isinstance(value, dict):
            continue
        error = value.get("error")
        if error:
            errors.append(str(error))
        for item in value.get("errors") or []:
            if isinstance(item, dict) and item.get("error"):
                errors.append(str(item["error"]))
    if not errors:
        return None
    joined = "; ".join(dict.fromkeys(errors))
    return joined[:1000]


def _rag_indexing_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    for value in result.values():
        if isinstance(value, dict) and isinstance(value.get("rag_indexing"), dict):
            return value["rag_indexing"]
    return None


def _document_store_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    for value in result.values():
        if isinstance(value, dict) and isinstance(value.get("document_store"), dict):
            return value["document_store"]
    return None


def _is_bot_not_in_chat_error(error_message: str | None) -> bool:
    if not error_message:
        return False
    return "230002" in error_message or "Bot/User can NOT be out of the chat" in error_message


def _is_calendar_authorization_error(error_message: str | None) -> bool:
    if not error_message:
        return False
    return "99992402" in error_message and "field validation failed" in error_message


def _mark_resource_access_blocked(
    resource: Resource,
    *,
    status: str,
    reason: str,
    error_message: str | None,
) -> None:
    config = dict(resource.config_json or {})
    governance = dict(config.get("governance") or {})
    governance.update(
        {
            "status": status,
            "reason": reason,
            "error_message": error_message,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    config["governance"] = governance
    resource.config_json = config


def _bind_work_events_to_resource(db: Session, *, resource: Resource, work_event_ids: list[str]) -> None:
    if not work_event_ids:
        return
    events = db.scalars(
        select(WorkEvent)
        .where(WorkEvent.company_id == resource.company_id)
        .where(WorkEvent.id.in_(work_event_ids))
    ).all()
    for event in events:
        event.resource_id = resource.id


def _apply_source_metadata_to_work_events(
    db: Session,
    *,
    resource: Resource,
    source: ResourceSource | None,
    work_event_ids: list[str],
) -> None:
    if not work_event_ids:
        return
    events = db.scalars(
        select(WorkEvent)
        .where(WorkEvent.company_id == resource.company_id)
        .where(WorkEvent.id.in_(work_event_ids))
    ).all()
    source_type = source.source_type if source else FEISHU_APP_IDENTITY
    source_account_id = source.source_account_id if source else (str(resource.app_config_id) if resource.app_config_id else None)
    visibility_scope = source.visibility_scope if source else (resource.permission_level or "company")
    for event in events:
        event.source_type = source_type
        event.source_account_id = source_account_id
        event.visibility_scope = visibility_scope
        event.data_classification = getattr(resource, "data_classification", "company")
        event.business_domain = getattr(resource, "business_domain", "general")
        event.allowed_user_ids = source.allowed_user_ids if source else []
        event.allowed_roles = source.allowed_roles if source else []
        event.allowed_departments = source.allowed_departments if source else []


def _index_resource_metadata_event(db: Session, *, resource: Resource, sync_action: dict[str, Any]) -> WorkEvent:
    now = datetime.now(UTC)
    title = resource.resource_name or resource.resource_id
    content = (
        f"资源索引：{title}\n"
        f"类型：{normalize_v5_resource_type(resource.resource_type, resource.resource_id)}\n"
        f"数据层：{sync_action['data_layer']}\n"
        f"查询路径：{sync_action['query_path']}\n"
        f"策略：{sync_action['reason']}\n"
        f"飞书资源 ID：{resource.resource_id}"
    )
    event = upsert_work_event(
        db,
        WorkEventCreate(
            company_id=resource.company_id,
            resource_id=resource.id,
            source="feishu",
            event_type="feishu.resource.index",
            external_id=f"resource-index:{resource.id}",
            title=f"飞书资源索引：{title}",
            content_text=content,
            occurred_at=now,
            labels=["feishu", "resource_index", sync_action["data_layer"]],
            payload={
                "resource_id": str(resource.id),
                "resource_type": normalize_v5_resource_type(resource.resource_type, resource.resource_id),
                "legacy_resource_type": resource.resource_type,
                "resource_name": resource.resource_name,
                "resource_external_id": resource.resource_id,
                "resource_sub_id": resource.resource_sub_id,
                "data_layer": sync_action["data_layer"],
                "sync_action": sync_action["action"],
                "query_path": sync_action["query_path"],
                "config_json": resource.config_json or {},
            },
            raw_json={},
            importance_score=0.2,
            sensitivity="normal",
        ),
    )
    event.resource_id = resource.id
    event.vector_status = "skipped"
    return event
