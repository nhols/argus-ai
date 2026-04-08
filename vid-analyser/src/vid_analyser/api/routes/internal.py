import asyncio
import os
import logging
import mimetypes
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from vid_analyser.auth import require_vid_analyser_api_key
from vid_analyser.config_schema import RunConfig
from vid_analyser.api.runtime import (
    require_active_run_config,
)
from vid_analyser.pipeline.run import run

logger = logging.getLogger(__name__)

SHARED_INPUT_ROOT_ENV_VAR = "VID_ANALYSER_SHARED_INPUT_ROOT"
UPLOAD_CHUNK_SIZE = 1024 * 1024


class AnalyseVideoMetadata(BaseModel):
    received_at: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class AnalyseSharedVideoRequest(AnalyseVideoMetadata):
    shared_video_path: str
    content_type: str | None = None


def _get_shared_input_root() -> Path | None:
    raw = os.getenv(SHARED_INPUT_ROOT_ENV_VAR)
    if raw is None or not raw.strip():
        return None
    return Path(raw).resolve()


def _resolve_shared_video_path(shared_video_path: str) -> Path:
    shared_root = _get_shared_input_root()
    if shared_root is None:
        raise HTTPException(status_code=503, detail="Shared video input is not configured")

    candidate = Path(shared_video_path)
    if not candidate.is_absolute():
        candidate = shared_root / candidate

    resolved = candidate.resolve()
    try:
        resolved.relative_to(shared_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Shared video path is outside the allowed root") from exc

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Shared video file was not found")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Shared video path is not a file")
    return resolved


async def _write_upload_to_temp_file(video: UploadFile) -> tuple[Path, int]:
    suffix = Path(video.filename or "upload.mp4").suffix or ".mp4"
    temp_path: Path | None = None
    file_size = 0

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            temp_path = Path(tmp_file.name)
            while chunk := await video.read(UPLOAD_CHUNK_SIZE):
                file_size += len(chunk)
                tmp_file.write(chunk)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    finally:
        await video.close()

    return temp_path, file_size


def _track_background_task(app: FastAPI, task: asyncio.Task) -> None:
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)


def _snapshot_active_run_config(app: FastAPI) -> tuple[RunConfig, int | None, str | None]:
    active_config = require_active_run_config(app)
    return (
        active_config.model_copy(deep=True),
        getattr(app.state, "run_config_version_id", None),
        getattr(app.state, "run_config_source", None),
    )


async def _run_analysis(
    app: FastAPI,
    *,
    request_name: str,
    video_path: Path,
    content_type: str,
    size_bytes: int,
    identifier: str,
    run_config: RunConfig,
    config_version_id: int | None,
    config_source: str | None,
) -> object:
    start = time.perf_counter()
    logger.info(
        "Starting %s identifier=%s content_type=%s size_bytes=%s config_id=%s",
        request_name,
        identifier,
        content_type,
        size_bytes,
        config_version_id,
    )
    try:
        response = await run(
            video_path=video_path,
            config=run_config,
            content_type=content_type,
            analysis_repository=app.state.analysis_repository,
            notification_repository=app.state.notification_repository,
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Completed %s identifier=%s size_bytes=%s duration_ms=%.2f config_id=%s",
            request_name,
            identifier,
            size_bytes,
            duration_ms,
            config_version_id,
        )
        return response
    except HTTPException:
        raise
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "%s failed identifier=%s size_bytes=%s duration_ms=%.2f config_id=%s",
            request_name,
            identifier,
            size_bytes,
            duration_ms,
            config_version_id,
        )
        raise


async def _background_analyse_video(
    app: FastAPI,
    *,
    request_name: str,
    video_path: Path,
    content_type: str,
    size_bytes: int,
    identifier: str,
    run_config: RunConfig,
    config_version_id: int | None,
    config_source: str | None,
    cleanup_path: Path | None = None,
) -> None:
    try:
        logger.info(
            "Background analysis queued request=%s identifier=%s config_id=%s source=%s",
            request_name,
            identifier,
            config_version_id,
            config_source,
        )
        semaphore = app.state.analysis_semaphore
        logger.info(
            "Waiting for analysis slot request=%s identifier=%s active_limit=%s",
            request_name,
            identifier,
            app.state.max_concurrent_jobs,
        )
        async with semaphore:
            logger.info(
                "Acquired analysis slot request=%s identifier=%s active_limit=%s",
                request_name,
                identifier,
                app.state.max_concurrent_jobs,
            )
            await _run_analysis(
                app,
                request_name=request_name,
                video_path=video_path,
                content_type=content_type,
                size_bytes=size_bytes,
                identifier=identifier,
                run_config=run_config,
                config_version_id=config_version_id,
                config_source=config_source,
            )
    except Exception:
        logger.exception(
            "Background analysis failed request=%s identifier=%s config_id=%s",
            request_name,
            identifier,
            config_version_id,
        )
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)
            logger.info("Deleted temp file %s", cleanup_path)


def _guess_content_type(path: Path, fallback: str | None = None) -> str:
    return fallback or mimetypes.guess_type(path.name)[0] or "video/mp4"


def _new_request_id() -> str:
    return uuid.uuid4().hex


router = APIRouter(prefix="/internal", dependencies=[Depends(require_vid_analyser_api_key)])


@router.post("/analyse-video")
async def analyse_video(
    request: Request,
    video: Annotated[UploadFile, File(...)],
    received_at: Annotated[str | None, Form()] = None,
    start_time: Annotated[str | None, Form()] = None,
    end_time: Annotated[str | None, Form()] = None,
):
    logger.info("Received analyse-video request from %s", request.client.host if request.client else "unknown")
    metadata = AnalyseVideoMetadata(received_at=received_at, start_time=start_time, end_time=end_time)

    temp_path, file_size = await _write_upload_to_temp_file(video)
    logger.info(
        "Parsed upload filename=%s content_type=%s size_bytes=%s metadata=%s",
        video.filename,
        video.content_type,
        file_size,
        metadata.model_dump(),
    )
    if file_size == 0:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    logger.info("Saved upload to temp file %s", temp_path)
    run_config, config_version_id, config_source = _snapshot_active_run_config(request.app)
    request_id = _new_request_id()
    task = asyncio.create_task(
        _background_analyse_video(
            request.app,
            request_name="analyse-video",
            video_path=temp_path,
            content_type=video.content_type or "video/mp4",
            size_bytes=file_size,
            identifier=video.filename or temp_path.name,
            run_config=run_config,
            config_version_id=config_version_id,
            config_source=config_source,
            cleanup_path=temp_path,
        ),
        name=f"analyse-video:{request_id}",
    )
    _track_background_task(request.app, task)
    return {"status": "accepted", "request_id": request_id}


@router.post("/analyse-video/shared")
async def analyse_shared_video(payload: AnalyseSharedVideoRequest, request: Request):
    logger.info("Received analyse-video/shared request from %s", request.client.host if request.client else "unknown")

    shared_video_path = _resolve_shared_video_path(payload.shared_video_path)
    file_size = shared_video_path.stat().st_size
    content_type = _guess_content_type(shared_video_path, payload.content_type)

    logger.info(
        "Using shared video path=%s content_type=%s size_bytes=%s metadata=%s",
        shared_video_path,
        content_type,
        file_size,
        payload.model_dump(exclude={"shared_video_path", "content_type"}),
    )
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Shared video is empty")

    run_config, config_version_id, config_source = _snapshot_active_run_config(request.app)
    request_id = _new_request_id()
    task = asyncio.create_task(
        _background_analyse_video(
            request.app,
            request_name="analyse-video/shared",
            video_path=shared_video_path,
            content_type=content_type,
            size_bytes=file_size,
            identifier=str(shared_video_path),
            run_config=run_config,
            config_version_id=config_version_id,
            config_source=config_source,
        ),
        name=f"analyse-video-shared:{request_id}",
    )
    _track_background_task(request.app, task)
    return {"status": "accepted", "request_id": request_id}
