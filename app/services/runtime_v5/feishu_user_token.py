from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Account, FeishuAppConfig
from app.services.feishu.client import FeishuClient
from app.services.feishu.resources import _user_access_token


@dataclass(frozen=True)
class FeishuUserTokenResolution:
    user_access_token: str | None
    authorization_status: str
    account_id: str = ""
    error: str = ""

    @property
    def authorized(self) -> bool:
        return bool(self.user_access_token) and self.authorization_status == "AUTHORIZED"


async def resolve_feishu_user_access_token(
    db: Session,
    *,
    company_id: Any,
    open_id: str,
    app_config: FeishuAppConfig | None = None,
    client: FeishuClient | None = None,
) -> FeishuUserTokenResolution:
    if not company_id:
        return FeishuUserTokenResolution(None, "UNKNOWN", error="missing_company_id")
    if not open_id:
        return FeishuUserTokenResolution(None, "IDENTITY_MISMATCH", error="missing_open_id")
    app_config = app_config or _active_feishu_app_config(db, company_id)
    if app_config is None:
        return FeishuUserTokenResolution(None, "UNKNOWN", error="missing_feishu_app_config")
    account = _feishu_user_account(db, company_id=company_id, open_id=open_id, app_config_id=str(app_config.id))
    if account is None:
        return FeishuUserTokenResolution(None, "MISSING_AUTHORIZATION", error="missing_feishu_user_account")
    token, error = await _user_access_token(
        db,
        account=account,
        app_config=app_config,
        client=client or FeishuClient(app_config),
    )
    if token:
        return FeishuUserTokenResolution(token, "AUTHORIZED", account_id=str(account.id))
    return FeishuUserTokenResolution(
        None,
        "EXPIRED_AUTHORIZATION" if error else "MISSING_AUTHORIZATION",
        account_id=str(account.id),
        error=error or "missing_user_access_token",
    )


def _active_feishu_app_config(db: Session, company_id: Any) -> FeishuAppConfig | None:
    return db.scalar(
        select(FeishuAppConfig)
        .where(FeishuAppConfig.company_id == company_id)
        .where(FeishuAppConfig.is_active.is_(True))
    )


def _feishu_user_account(
    db: Session,
    *,
    company_id: Any,
    open_id: str,
    app_config_id: str,
) -> Account | None:
    accounts = db.scalars(
        select(Account)
        .where(Account.company_id == company_id)
        .where(Account.provider == "feishu_user")
        .where(Account.is_active.is_(True))
    ).all()
    for account in accounts:
        settings = account.settings or {}
        if str(settings.get("open_id") or "") != open_id:
            continue
        if app_config_id and str(settings.get("feishu_app_config_id") or "") not in {"", app_config_id}:
            continue
        return account
    return None
