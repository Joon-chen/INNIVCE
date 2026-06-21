from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter()
SIDEPANEL_FILE = Path(__file__).resolve().parents[2] / "static" / "portal" / "sidepanel.html"


@router.get("/sidepanel", response_class=FileResponse)
def sidepanel() -> FileResponse:
    return FileResponse(SIDEPANEL_FILE)
