#!/usr/bin/env python3
"""Bootstrap one company's Feishu app config for V5 local-prod/production."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.serialization import json_safe
from app.db.session import SessionLocal
from app.models.entities import BotUserAccess, Company, FeishuAppConfig


REQUIRED_ENV = ("V5_COMPANY_NAME", "V5_COMPANY_CODE", "V5_FEISHU_APP_SECRET")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _required_env() -> dict[str, str]:
    defaults = _inferred_cli_defaults()
    missing = [name for name in REQUIRED_ENV if not _env(name)]
    if not _env("V5_FEISHU_APP_ID", defaults.get("V5_FEISHU_APP_ID")):
        missing.append("V5_FEISHU_APP_ID")
    if missing:
        raise SystemExit(f"Missing required env: {', '.join(missing)}")
    values = {name: _env(name) or "" for name in REQUIRED_ENV}
    values["V5_FEISHU_APP_ID"] = _env("V5_FEISHU_APP_ID", defaults.get("V5_FEISHU_APP_ID")) or ""
    values["V5_FEISHU_CLI_PROFILE"] = _env("V5_FEISHU_CLI_PROFILE", defaults.get("V5_FEISHU_CLI_PROFILE")) or ""
    return values


def _settings_payload() -> dict[str, Any]:
    settings: dict[str, Any] = {}
    cli_profile = _env("V5_FEISHU_CLI_PROFILE", _env("LARK_PROFILE", _inferred_cli_defaults().get("V5_FEISHU_CLI_PROFILE")))
    if cli_profile:
        settings["cli_profile"] = cli_profile
        settings["cli_profile_source"] = "lark_cli_home" if not _env("V5_FEISHU_CLI_PROFILE") else "env"
    return settings


def _lark_cli_config_path() -> Path:
    cli_home = _env("LARK_CLI_HOME")
    return Path(cli_home).expanduser() / "config.json" if cli_home else Path.home() / ".lark-cli" / "config.json"


def _inferred_cli_defaults() -> dict[str, str]:
    config_path = _lark_cli_config_path()
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    apps = data.get("apps")
    if not isinstance(apps, list) or not apps:
        return {}
    preferred_profile = _env("V5_FEISHU_CLI_PROFILE", _env("LARK_PROFILE"))
    selected = None
    if preferred_profile:
        selected = next((app for app in apps if isinstance(app, dict) and app.get("name") == preferred_profile), None)
    if selected is None and len(apps) == 1 and isinstance(apps[0], dict):
        selected = apps[0]
    if not isinstance(selected, dict):
        return {}
    app_id = str(selected.get("appId") or selected.get("app_id") or "").strip()
    profile = str(selected.get("name") or preferred_profile or "").strip()
    result: dict[str, str] = {}
    if app_id:
        result["V5_FEISHU_APP_ID"] = app_id
    if profile:
        result["V5_FEISHU_CLI_PROFILE"] = profile
    return result


def main() -> int:
    values = _required_env()
    app_name = _env("V5_FEISHU_APP_NAME", "大飞哥") or "大飞哥"
    verification_token = _env("V5_FEISHU_VERIFICATION_TOKEN")
    encrypt_key = _env("V5_FEISHU_ENCRYPT_KEY")
    settings_patch = _settings_payload()
    bot_admin_open_id = _env("V5_BOT_ADMIN_OPEN_ID")
    bot_admin_name = _env("V5_BOT_ADMIN_NAME")
    app_config_id = UUID(raw_app_config_id) if (raw_app_config_id := _env("V5_FEISHU_APP_CONFIG_ID")) else None

    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.code == values["V5_COMPANY_CODE"]))
        company_created = False
        if company is None:
            company = Company(
                name=values["V5_COMPANY_NAME"],
                code=values["V5_COMPANY_CODE"],
                metadata_json={"source": "v5_bootstrap"},
            )
            db.add(company)
            db.flush()
            company_created = True

        app_config = db.get(FeishuAppConfig, app_config_id) if app_config_id else None
        app_by_app_id = db.scalar(
            select(FeishuAppConfig).where(FeishuAppConfig.app_id == values["V5_FEISHU_APP_ID"])
        )
        if app_config is not None and app_config.app_id != values["V5_FEISHU_APP_ID"]:
            raise SystemExit("V5_FEISHU_APP_CONFIG_ID belongs to a different Feishu app_id")
        if app_config is not None and app_by_app_id is not None and app_by_app_id.id != app_config.id:
            raise SystemExit("V5_FEISHU_APP_ID and V5_FEISHU_APP_CONFIG_ID point to different records")
        if app_config is None:
            app_config = app_by_app_id
        app_created = False
        if app_config is not None and app_config.company_id != company.id:
            raise SystemExit("Feishu app_id already belongs to another company")
        if app_config is None:
            app_values = {
                "company_id": company.id,
                "name": app_name,
                "app_id": values["V5_FEISHU_APP_ID"],
                "app_secret": values["V5_FEISHU_APP_SECRET"],
                "verification_token": verification_token,
                "encrypt_key": encrypt_key,
                "settings": json_safe(settings_patch),
                "is_active": True,
            }
            if app_config_id:
                app_values["id"] = app_config_id
            app_config = FeishuAppConfig(**app_values)
            db.add(app_config)
            app_created = True
        else:
            app_config.name = app_name
            app_config.app_secret = values["V5_FEISHU_APP_SECRET"]
            app_config.verification_token = verification_token
            app_config.encrypt_key = encrypt_key
            app_config.settings = json_safe({**(app_config.settings or {}), **settings_patch})
            app_config.is_active = True

        bot_admin = None
        if bot_admin_open_id:
            bot_admin = db.scalar(
                select(BotUserAccess)
                .where(BotUserAccess.company_id == company.id)
                .where(BotUserAccess.open_id == bot_admin_open_id)
            )
            if bot_admin is None:
                bot_admin = BotUserAccess(company_id=company.id, open_id=bot_admin_open_id)
                db.add(bot_admin)
            bot_admin.display_name = bot_admin_name
            bot_admin.role = "owner"
            bot_admin.access_scope = "company"
            bot_admin.is_active = True

        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise SystemExit(f"Invalid or duplicate bootstrap data: {exc}") from exc

        output = {
            "ok": True,
            "company": {
                "id": str(company.id),
                "name": company.name,
                "code": company.code,
                "created": company_created,
            },
            "feishu_app": {
                "id": str(app_config.id),
                "name": app_config.name,
                "app_id": app_config.app_id,
                "created": app_created,
                "is_active": app_config.is_active,
                "cli_profile": (app_config.settings or {}).get("cli_profile"),
            },
            "bot_admin": (
                {"open_id": bot_admin.open_id, "role": bot_admin.role, "created_or_updated": True}
                if bot_admin
                else None
            ),
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
