from typing import Any


def accessible_mailboxes_payload() -> dict[str, Any]:
    return {
        "available": False,
        "reason": "飞书邮箱没有列出所有可访问邮箱的 tenant API。请使用你的飞书邮箱地址作为 user_mailbox_id。",
        "next_step": "调用 /mail/folders，Body 填 {\"user_mailbox_id\":\"你的飞书邮箱地址\"}，拿到 folder_id 后再同步 mail。",
        "examples": [
            {"user_mailbox_id": "name@example.com", "folder_id": "INBOX"},
            {"user_mailbox_id": "me", "note": "me 仅适用于 user_access_token，不适用于当前 tenant_access_token 模式"},
        ],
    }
