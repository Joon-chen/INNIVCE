from typing import Any


def feishu_app_cli_profile(app_config: Any) -> str | None:
    settings = getattr(app_config, "settings", None)
    if not isinstance(settings, dict):
        return None
    for key in ("cli_profile", "lark_cli_profile"):
        value = str(settings.get(key) or "").strip()
        if value:
            return value
    return None
