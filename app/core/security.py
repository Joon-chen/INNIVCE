import re
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEYS = {
    "password",
    "pass",
    "secret",
    "client_secret",
    "app_secret",
    "access_token",
    "refresh_token",
    "tenant_access_token",
    "authorization",
    "cookie",
}

IDENTIFIER_KEYS = {
    "approval_code",
    "instance_code",
    "app_token",
    "table_id",
    "chat_id",
    "open_id",
    "external_id",
    "work_event_id",
    "source_event_id",
    "evidence_event_id",
}

EMAIL_RE = re.compile(r"(?P<name>[A-Za-z0-9._%+-]{1,64})@(?P<domain>[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d -]{7,}\d)(?!\d)")
ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ][0-2]\d:[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?"
    r"(?:Z|[+-][0-2]\d:?[0-5]\d)?)?$"
)


def mask_email(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group("name")
        domain = match.group("domain")
        visible = name[:2] if len(name) > 2 else name[:1]
        return f"{visible}***@{domain}"

    return EMAIL_RE.sub(repl, value)


def redact_text(value: str) -> str:
    if ISO_DATETIME_RE.fullmatch(value.strip()):
        return value
    value = mask_email(value)
    return PHONE_RE.sub("[PHONE_REDACTED]", value)


def redact_payload(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            key_lower = str(key).lower()
            if key_lower in SENSITIVE_KEYS or any(part in key_lower for part in SENSITIVE_KEYS):
                redacted[key] = "[REDACTED]"
            elif key_lower in IDENTIFIER_KEYS:
                redacted[key] = value
            else:
                redacted[key] = redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    if isinstance(payload, str):
        return redact_text(payload)
    return payload
