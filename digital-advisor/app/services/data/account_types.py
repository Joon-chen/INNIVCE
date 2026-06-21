COMPANY_FEISHU_APP = "company_feishu_app"
PERSONAL_FEISHU_USER = "personal_feishu_user"
FEISHU_MAIL = "feishu_mail"
EXTERNAL_MAIL = "external_mail"
PERSONAL_DINGTALK = "personal_dingtalk"

ACCOUNT_TYPES = {
    COMPANY_FEISHU_APP,
    PERSONAL_FEISHU_USER,
    FEISHU_MAIL,
    EXTERNAL_MAIL,
    PERSONAL_DINGTALK,
}

USER_IDENTITY_ACCOUNT_TYPES = {
    PERSONAL_FEISHU_USER,
    EXTERNAL_MAIL,
    PERSONAL_DINGTALK,
}

ACCOUNT_TYPE_ALIASES = {
    COMPANY_FEISHU_APP: COMPANY_FEISHU_APP,
    "feishu_app": COMPANY_FEISHU_APP,
    "lark_app": COMPANY_FEISHU_APP,
    "tenant_app": COMPANY_FEISHU_APP,
    PERSONAL_FEISHU_USER: PERSONAL_FEISHU_USER,
    "feishu_user": PERSONAL_FEISHU_USER,
    "lark_user": PERSONAL_FEISHU_USER,
    FEISHU_MAIL: FEISHU_MAIL,
    "feishu_mailbox": FEISHU_MAIL,
    "lark_mail": FEISHU_MAIL,
    "lark_mailbox": FEISHU_MAIL,
    EXTERNAL_MAIL: EXTERNAL_MAIL,
    "mail": EXTERNAL_MAIL,
    "email": EXTERNAL_MAIL,
    "mail_account": EXTERNAL_MAIL,
    "external_mail_account": EXTERNAL_MAIL,
    "imap": EXTERNAL_MAIL,
    "gmail": EXTERNAL_MAIL,
    "graph": EXTERNAL_MAIL,
    "outlook": EXTERNAL_MAIL,
    PERSONAL_DINGTALK: PERSONAL_DINGTALK,
    "dingtalk": PERSONAL_DINGTALK,
    "dingtalk_user": PERSONAL_DINGTALK,
    "personal_dingtalk_account": PERSONAL_DINGTALK,
}


def normalize_account_type(account_type: str | None = None, *, provider: str | None = None) -> str | None:
    explicit = _normalize_key(account_type)
    if explicit:
        return ACCOUNT_TYPE_ALIASES.get(explicit)
    return ACCOUNT_TYPE_ALIASES.get(_normalize_key(provider))


def require_account_type(account_type: str | None = None, *, provider: str | None = None) -> str:
    normalized = normalize_account_type(account_type, provider=provider)
    if normalized is None:
        raise ValueError("Unsupported account provider or account_type")
    return normalized


def is_external_mail_provider(provider: str | None) -> bool:
    return normalize_account_type(provider=provider) == EXTERNAL_MAIL


def is_user_identity_account_type(account_type: str | None = None, *, provider: str | None = None) -> bool:
    normalized = normalize_account_type(account_type, provider=provider)
    return normalized in USER_IDENTITY_ACCOUNT_TYPES


def _normalize_key(value: str | None) -> str:
    return (value or "").strip().lower()
