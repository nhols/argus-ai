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

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from vid_analyser.db import ExecutionRepository, ExecutionStatus, NotificationStatus, VideoUploadStatus, init_database
from vid_analyser.notifications import NotificationService, TelegramNotificationService
from vid_analyser.pipeline import RunConfig, run
from vid_analyser.llm.response_model import AnalyseResponse

logger = logging.getLogger(__name__)

CONFIG_S3_BUCKET_ENV_VAR = "VID_ANALYSER_CONFIG_S3_BUCKET"
CONFIG_S3_KEY_ENV_VAR = "VID_ANALYSER_CONFIG_S3_KEY"
VIDEO_S3_BUCKET_ENV_VAR = "VID_ANALYSER_VIDEO_S3_BUCKET"
VIDEO_S3_PREFIX_ENV_VAR = "VID_ANALYSER_VIDEO_S3_PREFIX"
SQLITE_PATH_ENV_VAR = "VID_ANALYSER_SQLITE_PATH"
TELEGRAM_BOT_TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
DEFAULT_CONFIG_S3_KEY = "config/run_config.json"
DEFAULT_VIDEO_S3_PREFIX = "videos"
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


@dataclass(slots=True)
class ExecutionContext:
    execution_id: str
    now: str
    video_s3_key: str
    notifications_configured: bool
    user_prompt: str
    system_prompt: str


def _load_run_config_document_from_s3(bucket: str, key: str) -> dict:
    import boto3

    logger.info("Loading run config from s3://%s/%s", bucket, key)
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


def _build_video_s3_key(*, execution_id: str, filename: str | None) -> str:
    safe_filename = filename or "video.mp4"
    prefix = os.getenv(VIDEO_S3_PREFIX_ENV_VAR, DEFAULT_VIDEO_S3_PREFIX).strip("/")
    return f"{prefix}/{execution_id}/{safe_filename}"


def _upload_video_to_s3(*, video_path: str | Path, bucket: str, key: str, content_type: str | None) -> None:
    import boto3

    extra_args = {"ContentType": content_type} if content_type else None
    s3 = boto3.client("s3")
    if extra_args:
        s3.upload_file(str(video_path), bucket, key, ExtraArgs=extra_args)
    else:
        s3.upload_file(str(video_path), bucket, key)


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


def _build_user_prompt(metadata: AnalyseVideoMetadata, *, base_prompt: str) -> str:
    metadata_dict = metadata.model_dump(exclude_none=True)

    lines = [base_prompt]
    if not metadata_dict:
        return base_prompt

    lines.extend(["", "Event metadata:"])
    for key, value in metadata_dict.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _build_execution_context(app: FastAPI, *, video: UploadFile, metadata: AnalyseVideoMetadata) -> ExecutionContext:
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
        video_s3_key=_build_video_s3_key(execution_id=execution_id, filename=video.filename),
        notifications_configured=notifications_configured,
        user_prompt=_build_user_prompt(metadata, base_prompt=configured_user_prompt),
        system_prompt=configured_system_prompt,
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
        config_snapshot=app.state.run_config_document,
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


def _store_video_in_s3(
    app: FastAPI,
    *,
    context: ExecutionContext,
    temp_path: Path,
    video: UploadFile,
) -> None:
    try:
        _upload_video_to_s3(
            video_path=temp_path,
            bucket=app.state.video_s3_bucket,
            key=context.video_s3_key,
            content_type=video.content_type,
        )
        app.state.execution_repository.update_execution(
            context.execution_id,
            updated_at=_utc_now(),
            input_video_s3_bucket=app.state.video_s3_bucket,
            input_video_s3_key=context.video_s3_key,
            video_upload_status=VideoUploadStatus.STORED,
            video_upload_error=None,
        )
        logger.info("Uploaded video to s3://%s/%s", app.state.video_s3_bucket, context.video_s3_key)
    except Exception:
        app.state.execution_repository.update_execution(
            context.execution_id,
            updated_at=_utc_now(),
            video_upload_status=VideoUploadStatus.FAILED,
            video_upload_error="Video upload failed",
        )
        logger.exception("Failed to upload video to S3 for execution_id=%s", context.execution_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    bucket = os.getenv(CONFIG_S3_BUCKET_ENV_VAR)
    if not bucket:
        raise RuntimeError(f"{CONFIG_S3_BUCKET_ENV_VAR} is not set")

    key = os.getenv(CONFIG_S3_KEY_ENV_VAR, DEFAULT_CONFIG_S3_KEY)
    app.state.run_config_document = _load_run_config_document_from_s3(bucket, key)
    app.state.run_config = RunConfig.from_json_text(json.dumps(app.state.run_config_document))
    app.state.notification_service = _build_notification_service()
    app.state.video_s3_bucket = os.getenv(VIDEO_S3_BUCKET_ENV_VAR, bucket)
    db_path = os.getenv(SQLITE_PATH_ENV_VAR, DEFAULT_SQLITE_PATH)
    init_database(db_path)
    app.state.execution_repository = ExecutionRepository(db_path)
    logger.info("Loaded run config from s3://%s/%s", bucket, key)
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/analyse-video")
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
        _store_video_in_s3(app, context=context, temp_path=temp_path, video=video)

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
