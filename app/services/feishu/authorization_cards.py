from typing import Any


def build_user_identity_authorization_card(
    *,
    answer: str,
    actions: list[dict[str, Any]],
    actor_open_id: str | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": _compact_answer(answer),
            },
        }
    ]
    buttons = []
    for action in actions[:limit]:
        url = str(action.get("url") or "").strip()
        if not url:
            continue
        buttons.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": str(action.get("label") or "去授权")[:30]},
                "type": "primary" if not buttons else "default",
                "url": url,
                "value": {
                    "kind": "user_identity_authorization",
                    "resource_type": str(action.get("resource_type") or ""),
                    "actor_open_id": actor_open_id or "",
                    "channel": str(action.get("channel") or ""),
                },
            }
        )
    if buttons:
        elements.append({"tag": "action", "layout": "flow", "actions": buttons})
    note = "授权完成后，再问同一个问题，我只会读取你本人授权范围内的数据。"
    if any(bool(action.get("local_only")) for action in actions):
        note = "当前授权入口使用本地地址；正式给员工使用前，需要先配置公网 HTTPS API_BASE_URL。CLI 二维码仅作为本地调试备用。"
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": note,
                }
            ],
        }
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "需要本人授权"},
        },
        "elements": elements,
    }


def _compact_answer(answer: str) -> str:
    lines = []
    for line in str(answer or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") or stripped.startswith("授权入口"):
            break
        lines.append(stripped)
    return "\n".join(lines)[:900] or "这个能力需要资源所有者本人先完成授权。"
