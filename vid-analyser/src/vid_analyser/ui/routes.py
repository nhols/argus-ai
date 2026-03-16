import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from vid_analyser.config_state import apply_config_update
from vid_analyser.storage.local import LocalStorageProvider

UI_BASIC_AUTH_USER_ENV_VAR = "UI_BASIC_AUTH_USER"
UI_BASIC_AUTH_PASSWORD_ENV_VAR = "UI_BASIC_AUTH_PASSWORD"

router = APIRouter(prefix="/ui", tags=["ui"])
_security = HTTPBasic()
_templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))


def _require_ui_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    expected_user = os.getenv(UI_BASIC_AUTH_USER_ENV_VAR)
    expected_password = os.getenv(UI_BASIC_AUTH_PASSWORD_ENV_VAR)
    if not expected_user or not expected_password:
        raise HTTPException(status_code=503, detail="UI auth is not configured")

    valid_user = secrets.compare_digest(credentials.username, expected_user)
    valid_password = secrets.compare_digest(credentials.password, expected_password)
    if valid_user and valid_password:
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid UI credentials",
        headers={"WWW-Authenticate": "Basic"},
    )


@router.get("")
def ui_root() -> RedirectResponse:
    return RedirectResponse(url="/ui/executions", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/executions", dependencies=[Depends(_require_ui_auth)])
def executions_page(request: Request):
    executions = request.app.state.execution_repository.list_executions(limit=100)
    execution_rows = []
    for execution in executions:
        analysis_result = _parse_json(execution.analysis_result_json)
        execution_rows.append(
            {
                "id": execution.id,
                "created_at": execution.created_at,
                "input_video_filename": execution.input_video_filename or "unknown",
                "status": execution.status,
                "notification_status": execution.notification_status or "unknown",
                "video_upload_status": execution.video_upload_status or "unknown",
                "message_for_user": analysis_result.get("message_for_user"),
            }
        )

    return _templates.TemplateResponse(
        request,
        "executions.html",
        {"executions": execution_rows, "page_title": "Executions"},
    )


@router.get("/executions/{execution_id}", dependencies=[Depends(_require_ui_auth)])
def execution_detail_page(request: Request, execution_id: str):
    execution = request.app.state.execution_repository.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")

    config_record = None
    config_json_pretty = None
    if execution.config_version_id:
        config_record = request.app.state.config_repository.get_config(execution.config_version_id)
        if config_record is not None:
            config_json_pretty = json.dumps(json.loads(config_record.config_json), indent=2, sort_keys=True)

    event_metadata_pretty = json.dumps(_parse_json(execution.event_metadata_json), indent=2, sort_keys=True)
    analysis_result = _parse_json(execution.analysis_result_json)
    analysis_result_pretty = json.dumps(analysis_result, indent=2, sort_keys=True) if analysis_result else None
    video_available = execution.video_storage_provider == "local" and bool(execution.video_storage_path)

    return _templates.TemplateResponse(
        request,
        "execution_detail.html",
        {
            "page_title": f"Execution {execution.id}",
            "execution": execution,
            "config_record": config_record,
            "config_json_pretty": config_json_pretty,
            "event_metadata_pretty": event_metadata_pretty,
            "analysis_result_pretty": analysis_result_pretty,
            "video_available": video_available,
        },
    )


@router.get("/executions/{execution_id}/video", dependencies=[Depends(_require_ui_auth)])
def execution_video(request: Request, execution_id: str):
    execution = request.app.state.execution_repository.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if execution.video_storage_provider != "local" or not execution.video_storage_path:
        raise HTTPException(status_code=404, detail="Local video not available")

    storage_provider = request.app.state.storage_provider
    if not isinstance(storage_provider, LocalStorageProvider):
        raise HTTPException(status_code=404, detail="Local video not available")

    video_path = storage_provider.resolve_path(execution.video_storage_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")

    return FileResponse(video_path, media_type=execution.input_video_content_type or "video/mp4")


@router.get("/config", dependencies=[Depends(_require_ui_auth)])
def config_page(request: Request, saved: int = 0):
    config_record = request.app.state.config_repository.get_latest_config()
    config_json_pretty = (
        json.dumps(json.loads(config_record.config_json), indent=2, sort_keys=True)
        if config_record is not None
        else ""
    )

    return _templates.TemplateResponse(
        request,
        "config.html",
        {
            "page_title": "Config",
            "config_record": config_record,
            "config_json_pretty": config_json_pretty,
            "saved": bool(saved),
        },
    )


@router.post("/config", dependencies=[Depends(_require_ui_auth)])
async def update_config_page(request: Request, config_json: str = Form(...)):
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc.msg}") from None

    try:
        apply_config_update(
            request.app,
            config=config,
            created_at=_utc_now(),
            source="ui",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config: {exc}") from None
    return RedirectResponse(url="/ui/config?saved=1", status_code=status.HTTP_303_SEE_OTHER)


def _parse_json(value: str | None) -> dict:
    if not value:
        return {}
    return json.loads(value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
