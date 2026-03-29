import logging
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from vid_analyser.auth import require_ui_basic_auth, require_vid_analyser_api_key
from vid_analyser.config_state import apply_config_update
from vid_analyser.db import ConfigRepository, ExecutionRepository, ExecutionStatus, NotificationStatus, VideoUploadStatus, init_database
from vid_analyser.notifications import NotificationService, TelegramNotificationService
from vid_analyser.pipeline import RunConfig, run
from vid_analyser.llm.response_model import AnalyseResponse
from vid_analyser.prompting import build_system_prompt, build_user_prompt
from vid_analyser.storage import build_storage_provider
from vid_analyser.ui import router as ui_router

logger = logging.getLogger(__name__)

SQLITE_PATH_ENV_VAR = "VID_ANALYSER_SQLITE_PATH"
TELEGRAM_BOT_TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
ENABLE_API_DOCS_ENV_VAR = "ENABLE_API_DOCS"
DEFAULT_SQLITE_PATH = "/app/data/vid_analyser.db"
DEFAULT_USER_PROMPT = "Analyse this doorbell video and return the required JSON response."
DEFAULT_SYSTEM_PROMPT = (
    "You are analysing footage from a fixed residential video doorbell camera. "
    "Return one JSON object matching the required schema exactly. "
    "Describe only visible facts and do not add extra keys."
)


class AnalyseVideoMetadata(BaseModel):
    received_at: str | None = None
    station_serial_number: str | None = None
    device_serial_number: str | None = None
    storage_path: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class ConfigUpdateRequest(BaseModel):
    config: dict
    source: str | None = "api"


@dataclass(slots=True)
class ExecutionContext:
    execution_id: str
    now: str
    notifications_configured: bool
    user_prompt: str
    system_prompt: str


def _load_json_document_from_s3(bucket: str, key: str) -> dict:
    import boto3

    logger.info("Loading JSON document from s3://%s/%s", bucket, key)
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read().decode("utf-8")
    return json.loads(body)


def _build_notification_service() -> NotificationService | None:
    token = os.getenv(TELEGRAM_BOT_TOKEN_ENV_VAR)
    if not token:
        logger.info("Telegram bot token not configured; notifications disabled")
        return None
    return TelegramNotificationService(token=token)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _is_api_docs_enabled() -> bool:
    raw = os.getenv(ENABLE_API_DOCS_ENV_VAR, "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _build_execution_context(app: FastAPI, *, video: UploadFile, metadata: AnalyseVideoMetadata) -> ExecutionContext:
    if app.state.run_config is None:
        raise RuntimeError("Run config is not initialized")
    now = _utc_now()
    execution_id = str(uuid4())
    configured_user_prompt = app.state.run_config.user_prompt or DEFAULT_USER_PROMPT
    configured_system_prompt = app.state.run_config.system_prompt or DEFAULT_SYSTEM_PROMPT
    notifications_configured = bool(
        app.state.run_config.telegram_chat_id and app.state.notification_service
    )
    return ExecutionContext(
        execution_id=execution_id,
        now=now,
        notifications_configured=notifications_configured,
        user_prompt=build_user_prompt(
            metadata=metadata,
            template=configured_user_prompt,
            load_json_document=_load_json_document_from_s3,
            execution_repository=app.state.execution_repository,
            previous_messages_limit=app.state.run_config.previous_messages_limit,
        ),
        system_prompt=build_system_prompt(
            metadata=metadata,
            template=configured_system_prompt,
            load_json_document=_load_json_document_from_s3,
            execution_repository=app.state.execution_repository,
            previous_messages_limit=app.state.run_config.previous_messages_limit,
        ),
    )


def _create_execution_record(
    app: FastAPI,
    *,
    context: ExecutionContext,
    video: UploadFile,
    metadata: AnalyseVideoMetadata,
    file_size: int,
) -> None:
    app.state.execution_repository.create_execution(
        execution_id=context.execution_id,
        created_at=context.now,
        updated_at=context.now,
        status=ExecutionStatus.RECEIVED,
        source="eufy-bridge",
        event_metadata=metadata.model_dump(exclude_none=True),
        input_video_filename=video.filename,
        input_video_content_type=video.content_type,
        input_video_size_bytes=file_size,
        video_upload_status=VideoUploadStatus.NOT_ATTEMPTED,
        device_serial_number=metadata.device_serial_number,
        station_serial_number=metadata.station_serial_number,
        event_start_time=metadata.start_time,
        event_end_time=metadata.end_time,
        notification_status=NotificationStatus.PENDING if context.notifications_configured else NotificationStatus.NOT_CONFIGURED,
        notification_channel="telegram" if app.state.run_config.telegram_chat_id else None,
        notification_target=app.state.run_config.telegram_chat_id,
        config_version_id=app.state.run_config_version_id,
    )


def _mark_execution_failed(app: FastAPI, execution_id: str, message: str) -> None:
    app.state.execution_repository.update_execution(
        execution_id,
        updated_at=_utc_now(),
        status=ExecutionStatus.FAILED,
        error_message=message,
    )


def _update_post_analysis_state(
    app: FastAPI,
    *,
    context: ExecutionContext,
    response: AnalyseResponse,
) -> None:
    app.state.execution_repository.update_execution(
        context.execution_id,
        updated_at=_utc_now(),
        status=ExecutionStatus.ANALYSED,
        analysis_result_json=response.model_dump(mode="json"),
        notification_status=(
            NotificationStatus.PENDING
            if response.send_notification and context.notifications_configured
            else NotificationStatus.NOT_REQUESTED
            if not response.send_notification
            else NotificationStatus.NOT_CONFIGURED
        ),
    )


async def _send_notification_if_needed(
    app: FastAPI,
    *,
    context: ExecutionContext,
    response: AnalyseResponse,
    temp_path: Path,
    video: UploadFile,
) -> None:
    if response.send_notification and context.notifications_configured:
        try:
            await app.state.notification_service.send_video(
                chat_id=app.state.run_config.telegram_chat_id,
                video_path=temp_path,
                caption=response.message_for_user,
            )
            app.state.execution_repository.update_execution(
                context.execution_id,
                updated_at=_utc_now(),
                status=ExecutionStatus.NOTIFIED,
                notification_status=NotificationStatus.SENT,
                notification_channel="telegram",
                notification_target=app.state.run_config.telegram_chat_id,
                notification_sent_at=_utc_now(),
                notification_error=None,
            )
            logger.info("Sent Telegram notification for filename=%s", video.filename)
        except Exception:
            app.state.execution_repository.update_execution(
                context.execution_id,
                updated_at=_utc_now(),
                notification_status=NotificationStatus.FAILED,
                notification_channel="telegram",
                notification_target=app.state.run_config.telegram_chat_id,
                notification_error="Telegram send failed",
            )
            logger.exception("Failed to send Telegram notification for filename=%s", video.filename)
    elif response.send_notification:
        app.state.execution_repository.update_execution(
            context.execution_id,
            updated_at=_utc_now(),
            notification_status=NotificationStatus.NOT_CONFIGURED,
        )


def _store_video(
    app: FastAPI,
    *,
    context: ExecutionContext,
    temp_path: Path,
    video: UploadFile,
) -> None:
    try:
        video_reference = app.state.storage_provider.store_video(
            execution_id=context.execution_id,
            filename=video.filename,
            source_path=temp_path,
            content_type=video.content_type,
        )
        app.state.execution_repository.update_execution(
            context.execution_id,
            updated_at=_utc_now(),
            video_storage_provider=video_reference.provider,
            video_storage_path=video_reference.path,
            video_upload_status=VideoUploadStatus.STORED,
            video_upload_error=None,
        )
        logger.info(
            "Stored video using provider=%s path=%s",
            video_reference.provider,
            video_reference.path,
        )
    except Exception:
        app.state.execution_repository.update_execution(
            context.execution_id,
            updated_at=_utc_now(),
            video_upload_status=VideoUploadStatus.FAILED,
            video_upload_error="Video upload failed",
        )
        logger.exception("Failed to store video for execution_id=%s", context.execution_id)


def _load_run_config_document(app: FastAPI) -> dict:
    latest_config = app.state.config_repository.get_latest_config()
    if latest_config is None:
        raise RuntimeError("No config found in SQLite.")
    return json.loads(latest_config.config_json)


def _set_active_config(app: FastAPI, record) -> None:
    app.state.run_config_version_id = record.id
    app.state.run_config_document = json.loads(record.config_json)
    app.state.run_config = RunConfig.from_json_text(record.config_json)


def _load_active_config(app: FastAPI) -> None:
    latest_config = app.state.config_repository.get_latest_config()
    if latest_config is None:
        app.state.run_config_version_id = None
        app.state.run_config_document = None
        app.state.run_config = None
        logger.warning("No config found in SQLite config_versions table")
        return
    _set_active_config(app, latest_config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    db_path = os.getenv(SQLITE_PATH_ENV_VAR, DEFAULT_SQLITE_PATH)
    init_database(db_path)
    app.state.execution_repository = ExecutionRepository(db_path)
    app.state.config_repository = ConfigRepository(db_path)
    _load_active_config(app)
    app.state.storage_provider = build_storage_provider()
    app.state.notification_service = _build_notification_service()
    if app.state.run_config is not None:
        logger.info("Loaded run config from SQLite config_versions table")
    yield


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if _is_api_docs_enabled() else None,
    redoc_url="/redoc" if _is_api_docs_enabled() else None,
    openapi_url="/openapi.json" if _is_api_docs_enabled() else None,
)
app.include_router(ui_router)


@app.get("/config", dependencies=[Depends(require_ui_basic_auth)])
async def get_config():
    if app.state.run_config_document is None or app.state.run_config_version_id is None:
        raise HTTPException(status_code=404, detail="Config not initialized")
    return {
        "id": app.state.run_config_version_id,
        "config": app.state.run_config_document,
    }


@app.put("/config", dependencies=[Depends(require_ui_basic_auth)])
async def update_config(payload: ConfigUpdateRequest):
    try:
        return apply_config_update(
            app,
            config=payload.config,
            created_at=_utc_now(),
            source=payload.source,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config: {exc}") from None


@app.post("/analyse-video", dependencies=[Depends(require_vid_analyser_api_key)])
async def analyse_video(
    request: Request,
    video: Annotated[UploadFile, File(...)],
    received_at: Annotated[str | None, Form()] = None,
    station_serial_number: Annotated[str | None, Form()] = None,
    device_serial_number: Annotated[str | None, Form()] = None,
    storage_path: Annotated[str | None, Form()] = None,
    start_time: Annotated[str | None, Form()] = None,
    end_time: Annotated[str | None, Form()] = None,
):
    logger.info("Received analyse-video request from %s", request.client.host if request.client else "unknown")
    if app.state.run_config is None:
        raise HTTPException(status_code=503, detail="Config not initialized")
    metadata = AnalyseVideoMetadata(
        received_at=received_at,
        station_serial_number=station_serial_number,
        device_serial_number=device_serial_number,
        storage_path=storage_path,
        start_time=start_time,
        end_time=end_time,
    )

    video_bytes = await video.read()
    file_size = len(video_bytes)
    context = _build_execution_context(app, video=video, metadata=metadata)
    _create_execution_record(
        app,
        context=context,
        video=video,
        metadata=metadata,
        file_size=file_size,
    )
    logger.info(
        "Parsed upload filename=%s content_type=%s size_bytes=%s metadata_keys=%s prompt_lengths user=%s system=%s",
        video.filename,
        video.content_type,
        file_size,
        sorted(metadata.model_dump(exclude_none=True).keys()),
        len(context.user_prompt),
        len(context.system_prompt),
    )
    if file_size == 0:
        _mark_execution_failed(app, context.execution_id, "Uploaded video is empty")
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    start = time.perf_counter()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
            tmp_file.write(video_bytes)
            temp_path = Path(tmp_file.name)
        logger.info("Saved upload to temp file %s", temp_path)

        logger.info("Starting analysis for filename=%s", video.filename)
        response = await run(
            video_path=temp_path,
            user_prompt=context.user_prompt,
            system_prompt=context.system_prompt,
            config=app.state.run_config,
        )
        _update_post_analysis_state(app, context=context, response=response)
        await _send_notification_if_needed(app, context=context, response=response, temp_path=temp_path, video=video)
        _store_video(app, context=context, temp_path=temp_path, video=video)

        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Completed analysis filename=%s size_bytes=%s duration_ms=%.2f",
            video.filename,
            file_size,
            duration_ms,
        )
        return response
    except HTTPException:
        raise
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        _mark_execution_failed(app, context.execution_id, "Video analysis failed")
        logger.exception(
            "Video analysis failed size_bytes=%s duration_ms=%.2f",
            file_size,
            duration_ms,
        )
        raise HTTPException(status_code=500, detail="Video analysis failed") from None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
            logger.info("Deleted temp file %s", temp_path)
