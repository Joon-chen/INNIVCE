from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter()
PORTAL_FILE = Path(__file__).resolve().parents[2] / "static" / "portal" / "index.html"


@router.get("/portal", response_class=FileResponse)
def portal() -> FileResponse:
    return FileResponse(PORTAL_FILE)
