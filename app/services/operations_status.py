from typing import Any
from uuid import UUID

import httpx
import redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import FeishuAppConfig, SyncRun, WorkEvent


def automation_status_payload() -> dict[str, Any]:
    return {
        "auto_feishu_sync": {
            "enabled": settings.auto_feishu_sync_enabled,
            "app_config_ids": settings.auto_feishu_app_config_ids,
            "kinds": settings.auto_feishu_sync_kinds,
            "interval_seconds": settings.auto_feishu_sync_interval_seconds,
            "limit": settings.auto_feishu_sync_limit,
            "mail_folder_id": settings.auto_feishu_mail_folder_id,
            "mailbox_id": settings.auto_feishu_mail_user_mailbox_id,
            "mailbox_configured": bool(settings.auto_feishu_mail_user_mailbox_id),
        },
        "auto_imap_sync": {
            "enabled": settings.auto_imap_sync_enabled,
            "interval_seconds": settings.auto_imap_interval_seconds,
            "folder": settings.auto_imap_folder,
        },
        "auto_daily_report": {
            "enabled": settings.auto_daily_report_enabled,
            "company_ids": settings.auto_daily_report_company_ids,
            "hour": settings.auto_daily_report_hour,
            "minute": settings.auto_daily_report_minute,
            "push_feishu": settings.auto_daily_report_push_feishu,
        },
        "feishu_ws": {
            "enabled": settings.feishu_ws_enabled,
            "default_app_config_id": settings.feishu_default_app_config_id,
        },
    }


def system_status_payload(db: Session) -> dict[str, Any]:
    return {
        "database": database_status(db),
        "redis": redis_status(),
        "qdrant": qdrant_status(),
        "ai": {
            "provider": settings.ai_provider,
            "bot_enabled": settings.openai_use_for_bot,
            "embedding_provider": settings.embedding_provider,
            "embedding_configured": settings.embedding_provider.lower().strip() == "local_hash"
            or bool(settings.openai_api_key),
        },
        "storage": {"minio_endpoint": settings.minio_endpoint, "bucket": settings.minio_bucket},
        "counts": data_counts(db),
        "sync": sync_status(db),
    }


def database_status(db: Session) -> dict[str, Any]:
    try:
        db.execute(select(1))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def redis_status() -> dict[str, Any]:
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        return {"ok": bool(client.ping())}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def qdrant_status() -> dict[str, Any]:
    try:
        response = httpx.get(f"{settings.qdrant_url.rstrip('/')}/collections", timeout=5)
        response.raise_for_status()
        collections = response.json().get("result", {}).get("collections", [])
        return {"ok": True, "collections": [item.get("name") for item in collections]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def data_counts(db: Session) -> dict[str, int]:
    return {
        "work_events": db.scalar(select(func.count()).select_from(WorkEvent)) or 0,
        "vector_indexed": db.scalar(select(func.count()).select_from(WorkEvent).where(WorkEvent.vector_status == "indexed")) or 0,
        "vector_pending": db.scalar(select(func.count()).select_from(WorkEvent).where(WorkEvent.vector_status == "pending")) or 0,
        "vector_skipped": db.scalar(select(func.count()).select_from(WorkEvent).where(WorkEvent.vector_status == "skipped")) or 0,
        "sync_runs": db.scalar(select(func.count()).select_from(SyncRun)) or 0,
    }


def sync_status(db: Session) -> dict[str, Any]:
    latest = db.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(5)).all()
    return {
        "latest": [
            {
                "provider": item.provider,
                "sync_type": item.sync_type,
                "status": item.status,
                "started_at": item.started_at.isoformat() if item.started_at else None,
                "saved_count": item.saved_count,
                "error_count": item.error_count,
            }
            for item in latest
        ]
    }


def default_company_id(db: Session) -> str | None:
    if settings.feishu_default_app_config_id:
        app_config = db.get(FeishuAppConfig, UUID(settings.feishu_default_app_config_id))
        if app_config:
            return str(app_config.company_id)
    company_id = db.scalar(
        select(WorkEvent.company_id).group_by(WorkEvent.company_id).order_by(func.count().desc()).limit(1)
    )
    return str(company_id) if company_id else None
