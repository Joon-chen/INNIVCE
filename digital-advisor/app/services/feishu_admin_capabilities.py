from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.services.audit import write_audit_log
from app.services.feishu import FeishuApprovalService, get_feishu_sync_plan, probe_feishu_capabilities


def feishu_sync_plan_payload() -> dict[str, Any]:
    return {"plan": get_feishu_sync_plan()}


async def probe_app_capabilities_payload(db: Session, app_config: FeishuAppConfig) -> dict[str, Any]:
    result = await probe_feishu_capabilities(app_config)
    write_audit_log(
        db,
        action="feishu.capabilities.probe",
        company_id=app_config.company_id,
        target_type="feishu_app",
        target_id=str(app_config.id),
        payload={"checked": list(result.get("capabilities", {}).keys())},
    )
    db.commit()
    return result


def approval_resources_payload(db: Session, app_config: FeishuAppConfig) -> dict[str, Any]:
    resources = FeishuApprovalService(app_config).list_approval_resources(db)
    return {
        "items": [
            {
                "approval_code": item.approval_code,
                "approval_name": item.approval_name,
                "source": item.source,
                "resource_id": item.resource_id,
                "legacy_resource_id": item.legacy_resource_id,
            }
            for item in resources
        ]
    }
