from datetime import UTC, datetime
from typing import Any


RESOURCE_ACCESS_DECISIONS = {
    "business_group": "业务群",
    "do_not_connect": "不接入",
    "ignore": "忽略",
    "owner_confirmed": "负责人已确认",
}


def set_resource_access_decision(resource: Any, *, decision: str | None, note: str | None = None) -> dict[str, Any]:
    config = dict(resource.config_json or {})
    governance = dict(config.get("governance") or {})
    if decision in {None, "", "reset"}:
        governance.pop("access_decision", None)
        governance.pop("access_decision_label", None)
        governance.pop("access_decision_note", None)
        governance.pop("access_decision_updated_at", None)
    else:
        if decision not in RESOURCE_ACCESS_DECISIONS:
            raise ValueError(f"Unsupported resource access decision: {decision}")
        governance["access_decision"] = decision
        governance["access_decision_label"] = RESOURCE_ACCESS_DECISIONS[decision]
        governance["access_decision_note"] = note or ""
        governance["access_decision_updated_at"] = datetime.now(UTC).isoformat()
    if governance:
        config["governance"] = governance
    else:
        config.pop("governance", None)
    resource.config_json = config
    return governance


def resource_access_decision(item: dict[str, Any]) -> str | None:
    governance = (item.get("config_json") or {}).get("governance") or {}
    decision = governance.get("access_decision") if isinstance(governance, dict) else None
    return str(decision) if decision else None
