from pathlib import Path

from fastapi import APIRouter, Depends
from starlette.responses import FileResponse, HTMLResponse

from vid_analyser.auth import require_ui_basic_auth

HTML_PATH = Path(__file__).with_name("config_admin.html")
ASSETS_DIR = Path(__file__).with_name("assets")
FAVICON_PNG_PATH = ASSETS_DIR / "favicon.png"
FAVICON_ICO_PATH = ASSETS_DIR / "favicon.ico"

router = APIRouter()
FAVICON_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@router.get("/", response_class=HTMLResponse, include_in_schema=False, dependencies=[Depends(require_ui_basic_auth)])
@router.get("/ui", response_class=HTMLResponse, include_in_schema=False, dependencies=[Depends(require_ui_basic_auth)])
async def config_ui() -> HTMLResponse:
    return HTMLResponse(HTML_PATH.read_text(encoding="utf-8"))


@router.get("/ui/assets/favicon.png", include_in_schema=False)
async def ui_favicon() -> FileResponse:
    return FileResponse(FAVICON_PNG_PATH, media_type="image/png", headers=FAVICON_HEADERS)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> FileResponse:
    return FileResponse(FAVICON_ICO_PATH, media_type="image/x-icon", headers=FAVICON_HEADERS)
