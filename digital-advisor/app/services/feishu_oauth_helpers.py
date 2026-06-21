from html import escape
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.models.entities import FeishuAppConfig
from app.services.feishu_admin_apps import exchange_and_store_feishu_user_token
from app.services.user_identity_authorizations import (
    mark_user_identity_authorization,
    parse_user_identity_oauth_state,
)


def app_config_id_from_oauth_state(state: str | None) -> UUID | None:
    text = str(state or "").strip()
    prefix = "app_config_id:"
    if not text.startswith(prefix):
        return None
    try:
        return UUID(text.removeprefix(prefix))
    except ValueError:
        return None


def callback_error_text(value: Any) -> str:
    if isinstance(value, dict):
        message = value.get("message") or value.get("error") or value.get("msg")
        body = value.get("body")
        if isinstance(body, dict):
            message = message or body.get("msg") or body.get("message")
        if message:
            return str(message)
    return str(value)[:500]


def feishu_oauth_callback_page(ok: bool, message: str, *, state: str | None) -> HTMLResponse:
    title = "授权成功" if ok else "授权失败"
    color = "#1f7a4d" if ok else "#b42318"
    safe_title = escape(title)
    safe_message = escape(message)
    safe_state = escape(state or "")
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>个人飞书账号{title}</title>
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


async def feishu_oauth_callback_payload(
    db: Session,
    *,
    code: str | None,
    state: str | None,
) -> HTMLResponse:
    if not code:
        return feishu_oauth_callback_page(False, "飞书授权未返回 code。", state=state)
    user_identity_state = parse_user_identity_oauth_state(state)
    app_config_id = user_identity_state.app_config_id if user_identity_state else app_config_id_from_oauth_state(state)
    if app_config_id is None:
        return feishu_oauth_callback_page(False, "授权 state 无法识别应用配置。", state=state)
    app_config = db.get(FeishuAppConfig, app_config_id)
    if app_config is None:
        return feishu_oauth_callback_page(False, "没有找到对应的飞书应用配置。", state=state)
    try:
        result = await exchange_and_store_feishu_user_token(
            db,
            app_config=app_config,
            code=code,
            audit_action="feishu.oauth.callback",
        )
    except HTTPException as exc:
        return feishu_oauth_callback_page(False, f"授权保存失败：{callback_error_text(exc.detail)}", state=state)
    account = result.get("account") or {}
    if user_identity_state is not None:
        if user_identity_state.resource_type != "personal_feishu" or user_identity_state.provider != "feishu":
            return feishu_oauth_callback_page(False, "授权 state 与个人飞书资源不匹配。", state=state)
        account_open_id = str((account.get("settings") or {}).get("open_id") or "").strip()
        if account_open_id != user_identity_state.open_id:
            return feishu_oauth_callback_page(False, "授权账号与发起授权的飞书用户不一致，未更新员工智能体授权。", state=state)
        mark_user_identity_authorization(
            db,
            company_id=user_identity_state.company_id,
            open_id=user_identity_state.open_id,
            resource_type="personal_feishu",
            status="authorized",
            provider="feishu",
            owner_open_id=account_open_id,
            metadata={"account_id": account.get("id"), "app_config_id": str(app_config_id)},
        )
    display_name = account.get("display_name") or account.get("external_account_id") or "个人飞书账号"
    return feishu_oauth_callback_page(True, f"{display_name} 已授权并保存。可以回到数字参谋后台继续自动发现资源。", state=state)
