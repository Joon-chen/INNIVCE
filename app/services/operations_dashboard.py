from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Account, Company, ExtractedItem, FeishuAppConfig, Report, WorkEvent
from app.services.operations_bot_users import count_bot_user_access
from app.services.operations_read_models import list_sync_run_payloads
from app.services.operations_resources import dashboard_resource_summary
from app.services.operations_status import data_counts, default_company_id, system_status_payload


def dashboard_overview_payload(db: Session) -> dict[str, Any]:
    item_counts = {
        item_type: db.scalar(
            select(func.count()).select_from(ExtractedItem).where(ExtractedItem.item_type == item_type)
        )
        or 0
        for item_type in ["task", "risk", "decision"]
    }
    source_counts = {
        source: count
        for source, count in db.execute(
            select(WorkEvent.source, func.count()).group_by(WorkEvent.source).order_by(func.count().desc())
        ).all()
    }
    resource_summary = dashboard_resource_summary(db)
    return {
        "counts": {
            **data_counts(db),
            "companies": db.scalar(select(func.count()).select_from(Company)) or 0,
            "accounts": db.scalar(select(func.count()).select_from(Account)) or 0,
            "feishu_apps": db.scalar(select(func.count()).select_from(FeishuAppConfig)) or 0,
            "resources": resource_summary["enabled_resources"],
            "reports": db.scalar(select(func.count()).select_from(Report)) or 0,
            "bot_users": count_bot_user_access(db),
            **item_counts,
        },
        "default_company_id": default_company_id(db),
        "default_app_config_id": settings.feishu_default_app_config_id,
        "default_mailbox_id": settings.auto_feishu_mail_user_mailbox_id,
        "source_counts": source_counts,
        "resource_counts": resource_summary["resource_counts"],
        "migration": resource_summary["migration"],
        "system": system_status_payload(db),
        "latest_sync_runs": list_sync_run_payloads(db, limit=8)["items"],
    }
