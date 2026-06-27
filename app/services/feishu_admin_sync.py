from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.services.audit import write_audit_log
from app.services.feishu import FeishuClient, FeishuContactService, discover_feishu_resources, ingest_feishu_message, sync_feishu_information
from app.services.organization_foundation import upsert_organization_snapshot


async def sync_chat_messages_payload(
    db: Session,
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    end_time = data.end_time or datetime.now(UTC)
    start_time = data.start_time or (end_time - timedelta(days=1))
    start_ts = to_epoch_seconds(start_time)
    end_ts = to_epoch_seconds(end_time)
    if start_ts >= end_ts:
        raise HTTPException(status_code=400, detail="start_time must be before end_time")

    client = FeishuClient(app_config)
    saved_ids: list[str] = []
    page_token: str | None = None
    page_count = 0
    while page_count < data.max_pages:
        body = await client.list_messages(
            container_id_type="chat",
            container_id=data.chat_id,
            start_time=start_ts,
            end_time=end_ts,
            page_size=data.page_size,
            page_token=page_token,
        )
        page_count += 1
        body_data = body.get("data") or {}
        for message in body_data.get("items") or []:
            event = ingest_feishu_message(db, app_config, message)
            saved_ids.append(str(event.id))
            if data.extract_items:
                from app.services.ai.extraction import extract_items_for_event

                extract_items_for_event(db, event)
        page_token = body_data.get("page_token")
        if not body_data.get("has_more") or not page_token:
            break

    write_audit_log(
        db,
        action="feishu.messages.sync",
        company_id=app_config.company_id,
        target_type="feishu_chat",
        target_id=data.chat_id,
        payload={
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "saved_count": len(saved_ids),
            "page_count": page_count,
        },
    )
    db.commit()
    return {"work_event_ids": saved_ids, "saved_count": len(saved_ids), "page_count": page_count}


def to_epoch_seconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


async def sync_app_information_payload(
    db: Session,
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    result = await sync_feishu_information(
        db,
        app_config,
        kinds=data.kinds,
        limit=data.limit,
        max_pages=data.max_pages,
        chat_id=data.chat_id,
        user_mailbox_id=data.user_mailbox_id,
        folder_id=data.folder_id,
        app_token=data.app_token,
        table_id=data.table_id,
        approval_code=data.approval_code,
        document_id=data.document_id,
        document_type=data.document_type,
        start_time=data.start_time,
        end_time=data.end_time,
        extract_items=data.extract_items,
    )
    write_audit_log(
        db,
        action="feishu.information.sync",
        company_id=app_config.company_id,
        target_type="feishu_app",
        target_id=str(app_config.id),
        payload={
            "kinds": data.kinds,
            "limit": data.limit,
            "saved_count": sum(item.get("saved_count", 0) for item in result.values() if isinstance(item, dict)),
        },
    )
    db.commit()
    return {"results": result}


async def sync_organization_foundation_payload(
    db: Session,
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    service = FeishuContactService(app_config)
    payload = await service.snapshot_organization(
        max_departments=data.max_departments,
        max_users=data.max_users,
    )
    run = upsert_organization_snapshot(
        db,
        company_id=app_config.company_id,
        payload=payload,
        source_system="feishu",
        sync_type=data.sync_type,
    )
    write_audit_log(
        db,
        action="organization.foundation.sync",
        company_id=app_config.company_id,
        target_type="feishu_app",
        target_id=str(app_config.id),
        payload={
            "sync_run_id": str(run.id),
            "sync_type": data.sync_type,
            "department_count": run.department_count,
            "user_count": run.user_count,
            "membership_count": run.membership_count,
        },
    )
    db.commit()
    return {
        "sync_run_id": str(run.id),
        "status": run.status,
        "department_count": run.department_count,
        "user_count": run.user_count,
        "membership_count": run.membership_count,
    }


async def discover_app_resources_payload(
    db: Session,
    app_config: FeishuAppConfig,
    data: Any,
) -> dict[str, Any]:
    if data.async_run:
        from app.tasks.celery_app import discover_feishu_resources_task

        payload = data.model_dump(mode="json")
        payload.pop("async_run", None)
        task = discover_feishu_resources_task.delay(str(app_config.id), payload)
        write_audit_log(
            db,
            action="feishu.resources.discover.queued",
            company_id=app_config.company_id,
            target_type="feishu_app",
            target_id=str(app_config.id),
            payload={
                "task_id": task.id,
                "kinds": data.kinds,
                "limit": data.limit,
                "folder_count": len(data.folder_tokens),
                "mailbox_count": len(data.mailbox_ids),
                "approval_discovery_mode": "automatic",
            },
        )
        db.commit()
        return {
            "queued": True,
            "task_id": task.id,
            "message": "资源发现已进入后台执行；可在资源登记和同步记录里查看结果。",
        }

    result = await discover_feishu_resources(
        db,
        app_config,
        kinds=data.kinds,
        mailbox_id=data.mailbox_id,
        mailbox_ids=data.mailbox_ids,
        app_tokens=data.app_tokens,
        bitable_tables=data.bitable_tables,
        document_ids=data.document_ids,
        wiki_space_ids=data.wiki_space_ids,
        folder_tokens=data.folder_tokens,
        docs_search_keywords=data.docs_search_keywords,
        approval_codes=data.approval_codes,
        limit=data.limit,
        include_local_mining=data.include_local_mining,
    )
    write_audit_log(
        db,
        action="feishu.resources.discover",
        company_id=app_config.company_id,
        target_type="feishu_app",
        target_id=str(app_config.id),
        payload={
            "kinds": data.kinds,
            "mailbox_id": data.mailbox_id,
            "mailbox_count": len(data.mailbox_ids),
            "app_token_count": len(data.app_tokens),
            "bitable_table_count": len(data.bitable_tables),
            "document_count": len(data.document_ids),
            "wiki_space_count": len(data.wiki_space_ids),
            "approval_discovery_mode": "automatic",
            "saved_count": result.get("saved_count", 0),
            "error_count": len(result.get("errors") or []),
        },
    )
    db.commit()
    return result
