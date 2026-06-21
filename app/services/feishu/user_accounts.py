from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.serialization import json_safe
from app.models.entities import Account, FeishuAppConfig, Resource
from app.services.data.account_types import PERSONAL_FEISHU_USER
from app.services.data.resource_sources import FEISHU_USER_IDENTITY
from app.services.resource_sources import source_priority, upsert_resource_source


def upsert_feishu_user_account(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    token_response: dict[str, Any],
    user_info_response: dict[str, Any] | None = None,
) -> Account:
    token_data = _response_data(token_response)
    user_data = _response_data(user_info_response or {})
    external_id = _first_text(token_data, user_data, keys=("union_id", "open_id", "user_id"))
    if not external_id:
        raise HTTPException(status_code=502, detail="Feishu OAuth response missing user identity")

    account = db.scalar(
        select(Account)
        .where(Account.company_id == app_config.company_id)
        .where(Account.provider == "feishu_user")
        .where(Account.external_account_id == external_id)
    )
    if account is None:
        account = Account(
            id=uuid4(),
            company_id=app_config.company_id,
            provider="feishu_user",
            account_type=PERSONAL_FEISHU_USER,
            external_account_id=external_id,
        )
        db.add(account)
    account.account_type = PERSONAL_FEISHU_USER

    display_name = _first_text(user_data, token_data, keys=("name", "en_name", "open_id", "user_id")) or "飞书个人账号"
    account.display_name = display_name
    account.email_address = _first_text(user_data, token_data, keys=("email", "enterprise_email"))
    account.credentials = json_safe(_credentials_from_token(token_data))
    account.settings = json_safe(
        {
            **(account.settings or {}),
            "feishu_app_config_id": str(app_config.id),
            "open_id": _first_text(user_data, token_data, keys=("open_id",)),
            "union_id": _first_text(user_data, token_data, keys=("union_id",)),
            "user_id": _first_text(user_data, token_data, keys=("user_id",)),
            "avatar_url": _first_text(user_data, token_data, keys=("avatar_url", "avatar_thumb")),
            "visibility_scope": "private",
        }
    )
    account.is_active = True
    return account


def refresh_feishu_user_account(
    account: Account,
    *,
    app_config: FeishuAppConfig,
    token_response: dict[str, Any],
) -> Account:
    token_data = _response_data(token_response)
    account.credentials = json_safe(
        {
            **(account.credentials or {}),
            **_credentials_from_token(token_data),
        }
    )
    account.settings = json_safe({**(account.settings or {}), "feishu_app_config_id": str(app_config.id)})
    account.is_active = True
    return account


def upsert_feishu_user_identity_resource(db: Session, *, account: Account, app_config: FeishuAppConfig) -> Resource:
    external_id = account.external_account_id or str(account.id)
    resource = db.scalar(
        select(Resource)
        .where(Resource.company_id == account.company_id)
        .where(Resource.platform == "feishu")
        .where(Resource.resource_type == "capability")
        .where(Resource.resource_id == f"feishu:user:{external_id}")
        .where(Resource.resource_sub_id.is_(None))
    )
    if resource is None:
        resource = Resource(
            id=uuid4(),
            company_id=account.company_id,
            platform="feishu",
            resource_type="capability",
            resource_id=f"feishu:user:{external_id}",
        )
        db.add(resource)
    resource.resource_name = f"个人飞书账号：{account.display_name}"
    resource.sync_mode = "manual"
    resource.permission_level = "private"
    resource.data_classification = "personal"
    resource.business_domain = "personal"
    resource.enabled = account.is_active
    resource.app_config_id = app_config.id
    resource.config_json = json_safe(
        {
            **(resource.config_json or {}),
            "source": "feishu_user_oauth",
            "account_id": str(account.id),
            "feishu_app_config_id": str(app_config.id),
            "open_id": (account.settings or {}).get("open_id"),
            "union_id": (account.settings or {}).get("union_id"),
            "user_id": (account.settings or {}).get("user_id"),
        }
    )
    upsert_resource_source(
        db,
        resource=resource,
        source_type=FEISHU_USER_IDENTITY,
        source_account_id=account.id,
        source_account_label=account.display_name,
        access_level="user_authorized",
        can_sync=account.is_active,
        priority=source_priority(FEISHU_USER_IDENTITY),
        visibility_scope="private",
        allowed_user_ids=[str(account.id)],
        settings={
            "provider": "feishu_user",
            "account_type": PERSONAL_FEISHU_USER,
            "feishu_app_config_id": str(app_config.id),
            "external_account_id": account.external_account_id,
        },
    )
    return resource


def sanitized_feishu_user_account(account: Account) -> dict[str, Any]:
    credentials = account.credentials or {}
    return {
        "id": str(account.id),
        "company_id": str(account.company_id),
        "provider": account.provider,
        "account_type": account.account_type,
        "display_name": account.display_name,
        "external_account_id": account.external_account_id,
        "email_address": account.email_address,
        "is_active": account.is_active,
        "settings": {
            key: value
            for key, value in (account.settings or {}).items()
            if key in {"feishu_app_config_id", "open_id", "union_id", "user_id", "visibility_scope"}
        },
        "token_expires_at": credentials.get("expires_at"),
        "refresh_expires_at": credentials.get("refresh_expires_at"),
    }


def _response_data(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, dict) else None
    return data if isinstance(data, dict) else response


def _credentials_from_token(data: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    expires_in = _int_or_none(data.get("expires_in"))
    refresh_expires_in = _int_or_none(data.get("refresh_expires_in"))
    result = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "token_type": data.get("token_type"),
        "expires_in": expires_in,
        "refresh_expires_in": refresh_expires_in,
        "updated_at": now.isoformat(),
    }
    if expires_in is not None:
        result["expires_at"] = (now + timedelta(seconds=expires_in)).isoformat()
    if refresh_expires_in is not None:
        result["refresh_expires_at"] = (now + timedelta(seconds=refresh_expires_in)).isoformat()
    return {key: value for key, value in result.items() if value is not None}


def _first_text(*payloads: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for payload in payloads:
        for key in keys:
            value = payload.get(key)
            if value:
                return str(value)
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
