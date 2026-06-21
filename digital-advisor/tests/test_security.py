from app.core.security import redact_payload, redact_text


def test_redact_payload_secrets() -> None:
    payload = {"password": "secret", "nested": {"access_token": "token", "ok": "value"}}
    assert redact_payload(payload)["password"] == "[REDACTED]"
    assert redact_payload(payload)["nested"]["access_token"] == "[REDACTED]"


def test_redact_payload_keeps_business_identifiers() -> None:
    payload = {"approval_code": "AE296D4C-57FA-4A96-9B65-597446041A36"}

    assert redact_payload(payload)["approval_code"] == "AE296D4C-57FA-4A96-9B65-597446041A36"


def test_redact_text_email_and_phone() -> None:
    text = redact_text("contact alice@example.com +86 138 0000 0000")
    assert "alice@example.com" not in text
    assert "[PHONE_REDACTED]" in text
