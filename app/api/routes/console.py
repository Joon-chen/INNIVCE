from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["console"])

CONSOLE_FILE = Path(__file__).resolve().parents[2] / "static" / "console" / "index.html"


@router.get("/console", response_class=FileResponse)
def console() -> FileResponse:
    return FileResponse(CONSOLE_FILE)
