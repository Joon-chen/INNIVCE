from fastapi import Header, HTTPException

from app.core.config import settings


def require_admin_api_token(x_admin_token: str | None = Header(default=None)) -> None:
    if not settings.admin_api_token:
        return
    if x_admin_token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="Invalid or missing admin API token")
