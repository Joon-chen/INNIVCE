from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["console"])

USER_AUTH_FILE = Path(__file__).resolve().parents[2] / "static" / "user_auth" / "feishu_cli.html"


@router.get("/user-auth/feishu-cli", response_class=FileResponse)
def feishu_cli_user_auth() -> FileResponse:
    return FileResponse(USER_AUTH_FILE)
