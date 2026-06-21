from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.serialization import json_safe
from app.models.entities import Account, Company, FeishuAppConfig
from app.services.audit import write_audit_log
from app.services.feishu import FEISHU_ROUTE_RULES, FeishuClient, route_rule_to_dict
from app.services.feishu.user_accounts import (
    refresh_feishu_user_account,
    sanitized_feishu_user_account,
    upsert_feishu_user_account,
    upsert_feishu_user_identity_resource,
)

FEISHU_APP_CREDENTIAL_STATUS_PENDING = "pending_validation"
FEISHU_APP_CREDENTIAL_STATUS_VALID = "valid"


def pending_credential_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(settings or {})
    data["credential_status"] = FEISHU_APP_CREDENTIAL_STATUS_PENDING
    data.pop("credential_validated_at", None)
    return data


def create_feishu_app_config(db: Session, payload: Any) -> FeishuAppConfig:
    if db.get(Company, payload.company_id) is None:
        raise HTTPException(status_code=400, detail="Invalid company_id")

    existing_app_id = db.scalar(select(FeishuAppConfig.id).where(FeishuAppConfig.app_id == payload.app_id))
    if existing_app_id is not None:
        raise HTTPException(status_code=400, detail="Duplicate app_id")

    app_config = FeishuAppConfig(
        company_id=payload.company_id,
        name=payload.name,
        app_id=payload.app_id,
        app_secret=payload.app_secret,
        verification_token=payload.verification_token,
        encrypt_key=payload.encrypt_key,
        settings=json_safe(pending_credential_settings(payload.settings)),
    )
    db.add(app_config)
    try:
        db.flush()
        write_audit_log(
            db,
            action="feishu_app.created",
            company_id=app_config.company_id,
            target_type="feishu_app",
            target_id=str(app_config.id),
            payload={
                "company_id": str(app_config.company_id),
                "name": app_config.name,
                "app_id": app_config.app_id,
                "credential_status": FEISHU_APP_CREDENTIAL_STATUS_PENDING,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or duplicate Feishu app config") from exc
    db.refresh(app_config)
    return app_config


async def refresh_tenant_access_token(db: Session, app_config: FeishuAppConfig) -> dict[str, Any]:
    await FeishuClient(app_config).get_tenant_access_token(force_refresh=True)
    app_config.settings = json_safe(
        {
            **(app_config.settings or {}),
            "credential_status": FEISHU_APP_CREDENTIAL_STATUS_VALID,
            "credential_validated_at": datetime.now(UTC).isoformat(),
            "credential_validation_method": "tenant_access_token",
        }
    )
    write_audit_log(
        db,
        action="feishu.token.refresh",
        company_id=app_config.company_id,
        target_type="feishu_app",
        target_id=str(app_config.id),
        payload={
            "cached": True,
            "refreshed": True,
            "credential_status": FEISHU_APP_CREDENTIAL_STATUS_VALID,
            "credential_validation_method": "tenant_access_token",
        },
    )
    db.commit()
    return {
        "ok": True,
        "cached": True,
        "credential_status": FEISHU_APP_CREDENTIAL_STATUS_VALID,
        "token_redacted": True,
    }


def feishu_client_routing_payload(app_config: FeishuAppConfig, path_or_key: str | None) -> dict[str, Any]:
    client = FeishuClient(app_config)
    selected = client.route_for(path_or_key) if path_or_key else None
    return {
        "sdk_installed": client.sdk.available,
        "sdk_import_error": client.sdk.import_error,
        "selected": route_rule_to_dict(selected) if selected else None,
        "rules": [route_rule_to_dict(rule) for rule in FEISHU_ROUTE_RULES],
        "notes": [
            "稳定成熟能力优先 SDK：IM 消息、通讯录、日历、会议、SDK 托管 token 生命周期。",
            "复杂/高级/新接口优先 Raw HTTP：Wiki、知识库、Bitable 高级能力、审批高级能力、资源发现、飞书邮箱、未来新接口。",
            "显式 tenant_access_token 调试接口仍使用 Raw HTTP + Redis 缓存；SDK 调用自身会托管 token。",
        ],
    }


def feishu_user_oauth_url_payload(app_config: FeishuAppConfig, state: str | None) -> dict[str, str]:
    oauth_state = state or f"app_config_id:{app_config.id}"
    return {"oauth_url": FeishuClient(app_config).build_user_oauth_url(state=oauth_state), "state": oauth_state}


async def exchange_and_store_feishu_user_token(
    db: Session,
    *,
    app_config: FeishuAppConfig,
    code: str,
    audit_action: str,
) -> dict[str, Any]:
    client = FeishuClient(app_config)
    result = await client.exchange_user_access_token(code=code)
    token_data = result.get("data") if isinstance(result.get("data"), dict) else result
    user_info: dict[str, Any] = {}
    if token_data.get("access_token"):
        user_info = await client.get_user_info(user_access_token=token_data["access_token"])
    account = upsert_feishu_user_account(
        db,
        app_config=app_config,
        token_response=result,
        user_info_response=user_info,
    )
    db.flush()
    resource = upsert_feishu_user_identity_resource(db, account=account, app_config=app_config)
    write_audit_log(
        db,
        action=audit_action,
        company_id=app_config.company_id,
        target_type="account",
        target_id=str(account.id),
        payload={"ok": True, "provider": "feishu_user", "resource_id": str(resource.id)},
    )
    db.commit()
    return {"ok": True, "account": sanitized_feishu_user_account(account), "resource_id": str(resource.id)}


def list_feishu_user_account_payloads(db: Session, app_config: FeishuAppConfig) -> dict[str, Any]:
    accounts = db.scalars(
        select(Account)
        .where(Account.company_id == app_config.company_id)
        .where(Account.provider == "feishu_user")
        .where(Account.settings["feishu_app_config_id"].astext == str(app_config.id))
        .order_by(Account.updated_at.desc())
    ).all()
    return {"items": [sanitized_feishu_user_account(account) for account in accounts]}


async def refresh_feishu_user_token_payload(
    db: Session,
    app_config: FeishuAppConfig,
    account_id: UUID,
) -> dict[str, Any]:
    account = db.get(Account, account_id)
    if account is None or account.company_id != app_config.company_id or account.provider != "feishu_user":
        raise HTTPException(status_code=404, detail="Feishu user account not found")
    refresh_token = (account.credentials or {}).get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Feishu user account has no refresh_token")
    result = await FeishuClient(app_config).refresh_user_access_token(refresh_token=refresh_token)
    refresh_feishu_user_account(account, app_config=app_config, token_response=result)
    resource = upsert_feishu_user_identity_resource(db, account=account, app_config=app_config)
    write_audit_log(
        db,
        action="feishu.oauth.refresh",
        company_id=app_config.company_id,
        target_type="account",
        target_id=str(account.id),
        payload={"ok": True, "provider": "feishu_user", "resource_id": str(resource.id)},
    )
    db.commit()
    return {"ok": True, "account": sanitized_feishu_user_account(account), "resource_id": str(resource.id)}
