from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.user_identity_authorizations import (
    mark_user_identity_authorization,
    parse_user_identity_oauth_state,
    user_identity_callback_page,
)


def mail_user_identity_oauth_callback(
    db: Session,
    *,
    code: str | None,
    state: str | None,
    expected_provider: str,
) -> HTMLResponse:
    if not code:
        return user_identity_callback_page(False, "邮箱授权未返回 code。", state=state)
    oauth_state = parse_user_identity_oauth_state(state)
    if oauth_state is None or oauth_state.resource_type != "external_mail" or oauth_state.provider != expected_provider:
        return user_identity_callback_page(False, "授权 state 无法识别个人邮箱资源。", state=state)
    mark_user_identity_authorization(
        db,
        company_id=oauth_state.company_id,
        open_id=oauth_state.open_id,
        resource_type="external_mail",
        status="authorization_pending_token_exchange",
        provider=expected_provider,
        owner_open_id=oauth_state.open_id,
        metadata={"callback_received": True},
    )
    return user_identity_callback_page(
        True,
        "已收到本人邮箱授权回调。系统完成 token 交换和安全存储前，不会读取该邮箱数据。",
        state=state,
    )


_mail_user_identity_oauth_callback = mail_user_identity_oauth_callback
