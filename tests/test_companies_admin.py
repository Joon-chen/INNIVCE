from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.entities import Company
from app.schemas.common import AccountCreate
from app.services.companies_admin import create_account_payload
from app.services.data.account_types import (
    COMPANY_FEISHU_APP,
    EXTERNAL_MAIL,
    PERSONAL_DINGTALK,
    PERSONAL_FEISHU_USER,
    is_user_identity_account_type,
)


def test_user_identity_account_types_are_not_admin_configured() -> None:
    assert is_user_identity_account_type(PERSONAL_FEISHU_USER)
    assert is_user_identity_account_type(EXTERNAL_MAIL)
    assert is_user_identity_account_type(PERSONAL_DINGTALK)
    assert not is_user_identity_account_type(COMPANY_FEISHU_APP)


@pytest.mark.parametrize(
    ("provider", "account_type"),
    [
        ("feishu_user", PERSONAL_FEISHU_USER),
        ("imap", EXTERNAL_MAIL),
        ("gmail", None),
        ("dingtalk", PERSONAL_DINGTALK),
    ],
)
def test_create_account_rejects_user_identity_credentials(provider: str, account_type: str | None) -> None:
    company_id = uuid4()
    db = SimpleNamespace(get=lambda model, id_: SimpleNamespace(id=id_) if model is Company and id_ == company_id else None)
    data = AccountCreate(
        company_id=company_id,
        provider=provider,
        account_type=account_type,
        display_name="个人资源",
        email_address="owner@example.com" if provider in {"imap", "gmail"} else None,
        credentials={"access_token": "manual-token", "password": "manual-password"},
    )

    with pytest.raises(HTTPException) as exc_info:
        create_account_payload(db, data)

    assert exc_info.value.status_code == 400
    assert "resource owner" in exc_info.value.detail
    assert "cannot configure personal credentials" in exc_info.value.detail
