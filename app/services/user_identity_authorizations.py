import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.serialization import json_safe
from app.models.entities import BotUserAccess, FeishuAppConfig
from app.services.tools.base import USER_IDENTITY_BUNDLE_RESOURCE, USER_IDENTITY_RESOURCES


USER_IDENTITY_STATE_PREFIX = "user_identity:"
LOCAL_API_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


@dataclass(frozen=True)
class UserIdentityOAuthState:
    company_id: UUID
    open_id: str
    resource_type: str
    provider: str
    app_config_id: UUID | None = None


def default_user_identity_authorizations(
    *,
    open_id: str,
    created_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    timestamp = created_at or datetime.now(UTC).isoformat()
    return {
        USER_IDENTITY_BUNDLE_RESOURCE: {
            "status": "not_authorized",
            "provider": None,
            "owner_open_id": open_id,
            "authorization_owner": "resource_owner",
            "authorization_policy": "owner_granted_tighten_only",
            "authorization_model": "bundle_authorization",
            "covered_resources": list(USER_IDENTITY_RESOURCES),
            "can_escalate_original_permissions": False,
            "created_at": timestamp,
        }
    }


def user_identity_authorization_url_status(base_url: str) -> dict[str, Any]:
    parsed = urlparse(str(base_url or "").strip())
    host = (parsed.hostname or "").lower()
    scheme = (parsed.scheme or "").lower()
    is_local_only = host in LOCAL_API_HOSTS
    return {
        "api_base_url": str(base_url or "").rstrip("/"),
        "api_base_url_scheme": scheme,
        "api_base_url_host": host,
        "employee_reachable": bool(scheme == "https" and host and not is_local_only),
        "local_only": is_local_only,
        "production_requirement": "Set API_BASE_URL to a public HTTPS origin before employee rollout.",
    }


def build_user_identity_oauth_state(
    *,
    company_id: UUID,
    open_id: str,
    resource_type: str,
    provider: str,
    app_config_id: UUID | None = None,
) -> str:
    payload: dict[str, str] = {
        "company_id": str(company_id),
        "open_id": open_id,
        "resource_type": resource_type,
        "provider": provider,
    }
    if app_config_id is not None:
        payload["app_config_id"] = str(app_config_id)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{USER_IDENTITY_STATE_PREFIX}{encoded}"


def parse_user_identity_oauth_state(state: str | None) -> UserIdentityOAuthState | None:
    text = str(state or "").strip()
    if not text.startswith(USER_IDENTITY_STATE_PREFIX):
        return None
    encoded = text.removeprefix(USER_IDENTITY_STATE_PREFIX)
    padding = "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}").decode())
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        company_id = UUID(str(payload.get("company_id") or ""))
        app_config_id = UUID(str(payload["app_config_id"])) if payload.get("app_config_id") else None
    except ValueError:
        return None
    open_id = str(payload.get("open_id") or "").strip()
    resource_type = str(payload.get("resource_type") or "").strip()
    provider = str(payload.get("provider") or "").strip()
    if not open_id or not resource_type or not provider:
        return None
    return UserIdentityOAuthState(
        company_id=company_id,
        open_id=open_id,
        resource_type=resource_type,
        provider=provider,
        app_config_id=app_config_id,
    )


def mark_user_identity_authorization(
    db: Session,
    *,
    company_id: UUID,
    open_id: str,
    resource_type: str,
    status: str,
    provider: str,
    owner_open_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> BotUserAccess:
    owner = (owner_open_id or open_id).strip()
    access = db.scalar(
        select(BotUserAccess)
        .where(BotUserAccess.company_id == company_id)
        .where(BotUserAccess.open_id == open_id)
    )
    if access is None:
        access = BotUserAccess(
            company_id=company_id,
            open_id=open_id,
            display_name=open_id,
            role="member",
            access_scope="personal",
            is_active=True,
        )
        db.add(access)
    settings_data = dict(access.settings or {})
    authorizations = settings_data.get("user_identity_authorizations")
    if not isinstance(authorizations, dict):
        authorizations = {}
    current = dict(authorizations.get(USER_IDENTITY_BUNDLE_RESOURCE) or {})
    now = datetime.now(UTC).isoformat()
    current.update(
        {
            "status": status,
            "provider": provider,
            "requested_resource_type": resource_type,
            "owner_open_id": owner,
            "authorization_owner": "resource_owner",
            "authorization_policy": "owner_granted_tighten_only",
            "authorization_model": "bundle_authorization",
            "covered_resources": list(USER_IDENTITY_RESOURCES),
            "can_escalate_original_permissions": False,
            "updated_at": now,
        }
    )
    if status in {"authorized", "connected"}:
        current["authorized_at"] = current.get("authorized_at") or now
    if metadata:
        current["metadata"] = {**(current.get("metadata") if isinstance(current.get("metadata"), dict) else {}), **metadata}
    authorizations[USER_IDENTITY_BUNDLE_RESOURCE] = current
    settings_data["open_id"] = settings_data.get("open_id") or open_id
    settings_data["user_identity_authorizations"] = authorizations
    access.settings = json_safe(settings_data)
    db.commit()
    db.refresh(access)
    return access


def default_feishu_app_for_user_identity(db: Session, *, company_id: UUID) -> FeishuAppConfig:
    app_config = db.scalar(
        select(FeishuAppConfig)
        .where(FeishuAppConfig.company_id == company_id)
        .where(FeishuAppConfig.is_active.is_(True))
        .order_by(FeishuAppConfig.updated_at.desc())
    )
    if app_config is None:
        raise HTTPException(status_code=404, detail="No active Feishu app config for company")
    return app_config


def user_identity_callback_page(ok: bool, message: str, *, state: str | None) -> HTMLResponse:
    title = "授权已记录" if ok else "授权失败"
    color = "#1f7a4d" if ok else "#b42318"
    safe_title = escape(title)
    safe_message = escape(message)
    safe_state = escape(state or "")
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f7f8fa; color: #1f2329; }}
    main {{ max-width: 560px; margin: 80px auto; background: white; border: 1px solid #dee0e3; border-radius: 12px; padding: 28px; box-shadow: 0 12px 32px rgba(31,35,41,.08); }}
    h1 {{ margin: 0 0 12px; color: {color}; font-size: 24px; }}
    p {{ line-height: 1.7; font-size: 15px; }}
    a {{ display: inline-block; margin-top: 16px; color: #1456f0; text-decoration: none; font-weight: 600; }}
    code {{ color: #646a73; word-break: break-all; }}
  </style>
</head>
<body>
  <main>
    <h1>{safe_title}</h1>
    <p>{safe_message}</p>
    <p>状态：<code>{safe_state}</code></p>
    <a href="/console">返回数字参谋后台</a>
  </main>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200 if ok else 400)
