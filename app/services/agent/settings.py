from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Company, CompanySetting


DEFAULT_AGENT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "default_model": "qwen-local",
    "planner_enabled": True,
    "max_tool_calls": 4,
    "max_planner_steps": 3,
    "memory_mode": "stable_facts_only",
    "answer_style": "concise_business",
    "trace_enabled": True,
    "allow_write_tools": True,
    "require_write_confirmation": True,
}

AGENT_SETTING_LIMITS = {
    "max_tool_calls": (1, 20),
    "max_planner_steps": (1, 10),
}


def get_company_agent_settings(db: Session, company_id: UUID) -> dict[str, Any]:
    setting = _ensure_company_setting(db, company_id)
    return _agent_settings_payload(company_id, setting)


def update_company_agent_settings(db: Session, company_id: UUID, updates: dict[str, Any]) -> dict[str, Any]:
    setting = _ensure_company_setting(db, company_id)
    settings = dict(setting.settings or {})
    current = normalize_agent_settings(settings.get("agent") or {})
    next_settings = normalize_agent_settings({**current, **updates})
    settings["agent"] = next_settings
    setting.settings = settings
    return _agent_settings_payload(company_id, setting)


def normalize_agent_settings(value: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_AGENT_SETTINGS, **(value or {})}
    for key, (minimum, maximum) in AGENT_SETTING_LIMITS.items():
        merged[key] = min(max(_int_value(merged.get(key), DEFAULT_AGENT_SETTINGS[key]), minimum), maximum)
    for key in ("enabled", "planner_enabled", "trace_enabled", "allow_write_tools", "require_write_confirmation"):
        merged[key] = bool(merged.get(key))
    for key in ("default_model", "memory_mode", "answer_style"):
        merged[key] = str(merged.get(key) or DEFAULT_AGENT_SETTINGS[key]).strip() or DEFAULT_AGENT_SETTINGS[key]
    if not merged["allow_write_tools"]:
        merged["require_write_confirmation"] = True
    return merged


def _agent_settings_payload(company_id: UUID, setting: CompanySetting) -> dict[str, Any]:
    return {
        "company_id": str(company_id),
        "settings": normalize_agent_settings((setting.settings or {}).get("agent") or {}),
        "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
    }


def _ensure_company_setting(db: Session, company_id: UUID) -> CompanySetting:
    setting = db.scalar(select(CompanySetting).where(CompanySetting.company_id == company_id))
    if setting:
        return setting
    company = db.get(Company, company_id)
    setting = CompanySetting(
        company_id=company_id,
        status=getattr(company, "status", "active") if company else "active",
        settings={"source": "agent_settings", "architecture": "v5"},
    )
    db.add(setting)
    db.flush()
    return setting


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
