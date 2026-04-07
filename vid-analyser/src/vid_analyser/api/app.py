import asyncio
import json
import logging
import mimetypes
import os
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import logfire
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from vid_analyser.auth import require_ui_basic_auth, require_vid_analyser_api_key
from vid_analyser.config_schema import RunConfig
from vid_analyser.db import ConfigUpdateRepository, SentNotificationRepository, VidAnalysisRepository, init_database
from vid_analyser.api.ui import router as ui_router
from vid_analyser.pipeline.run import run
from vid_analyser.storage import build_storage_provider

load_dotenv()

logger = logging.getLogger(__name__)

SQLITE_PATH_ENV_VAR = "VID_ANALYSER_SQLITE_PATH"
TELEGRAM_BOT_TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
ENABLE_API_DOCS_ENV_VAR = "ENABLE_API_DOCS"
DEFAULT_SQLITE_PATH = "/app/data/vid_analyser.db"
SHARED_INPUT_ROOT_ENV_VAR = "VID_ANALYSER_SHARED_INPUT_ROOT"
MAX_CONCURRENT_JOBS_ENV_VAR = "VID_ANALYSER_MAX_CONCURRENT_JOBS"
UPLOAD_CHUNK_SIZE = 1024 * 1024


def configure_logfire(app: FastAPI) -> None:
    try:
        if not os.getenv("LOGFIRE_TOKEN"):
            logger.info("Logfire token not configured; skipping FastAPI and Pydantic AI instrumentation")
            return
        logfire.configure()
        logfire.instrument_pydantic_ai()
        logfire.instrument_fastapi(app)
    except Exception:
        logger.warning("Logfire not configured; skipping FastAPI and Pydantic AI instrumentation")


class AnalyseVideoMetadata(BaseModel):
    received_at: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class AnalyseSharedVideoRequest(AnalyseVideoMetadata):
    shared_video_path: str
    content_type: str | None = None


class ConfigUpdateRequest(BaseModel):
    config: RunConfig
    source: str | None = "api"


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


def _config_summary(config: RunConfig | None) -> dict[str, object]:
    if config is None:
        return {"configured": False}
    overlay_zones = len(config.overlay.zones) if config.overlay is not None else 0
    return {
        "configured": True,
        "overlay_zones": overlay_zones,
        "has_video_prompt": bool(config.video_analyser_sys_prompt),
        "has_notifier_prompt": bool(config.notifier_sys_prompt),
        "has_notifier_style": bool(config.notifier_style),
        "telegram_enabled": bool(config.telegram_chat_id),
        "previous_messages_limit": config.previous_messages_limit,
        "get_bookings": config.get_bookings,
    }


def _get_max_concurrent_jobs() -> int:
    raw = os.getenv(MAX_CONCURRENT_JOBS_ENV_VAR, "1").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{MAX_CONCURRENT_JOBS_ENV_VAR} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise RuntimeError(f"{MAX_CONCURRENT_JOBS_ENV_VAR} must be >= 1, got {value}")
    return value


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


def _track_background_task(task: asyncio.Task) -> None:
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)


def _clear_active_config_state(app: FastAPI) -> None:
    app.state.run_config = None
    app.state.run_config_version_id = None
    app.state.run_config_source = None


def _set_active_config_state(
    app: FastAPI,
    *,
    run_config: RunConfig,
    version_id: int,
    source: str | None,
) -> None:
    app.state.run_config = run_config
    app.state.run_config_version_id = version_id
    app.state.run_config_source = source
    logger.info(
        "Active run config set id=%s source=%s summary=%s",
        version_id,
        source,
        _config_summary(run_config),
    )


def _require_active_run_config(app: FastAPI) -> RunConfig:
    run_config = getattr(app.state, "run_config", None)
    if run_config is None:
        raise HTTPException(status_code=503, detail="Config not initialized")
    logger.info(
        "Using active run config id=%s source=%s summary=%s",
        getattr(app.state, "run_config_version_id", None),
        getattr(app.state, "run_config_source", None),
        _config_summary(run_config),
    )
    return run_config


async def _run_analysis(
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


def _snapshot_active_run_config() -> tuple[RunConfig, int | None, str | None]:
    active_config = _require_active_run_config(app)
    return (
        active_config.model_copy(deep=True),
        getattr(app.state, "run_config_version_id", None),
        getattr(app.state, "run_config_source", None),
    )


async def _background_analyse_video(
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    db_path = os.getenv(SQLITE_PATH_ENV_VAR, DEFAULT_SQLITE_PATH)
    session_factory = await init_database(db_path)
    app.state.config_repository = ConfigUpdateRepository(session_factory)
    app.state.notification_repository = SentNotificationRepository(session_factory)
    app.state.analysis_repository = VidAnalysisRepository(session_factory)
    app.state.storage_provider = build_storage_provider()
    app.state.background_tasks = set()
    app.state.max_concurrent_jobs = _get_max_concurrent_jobs()
    app.state.analysis_semaphore = asyncio.Semaphore(app.state.max_concurrent_jobs)
    logger.info("Configured max concurrent analysis jobs=%s", app.state.max_concurrent_jobs)
    latest_config = await app.state.config_repository.get_latest()
    if latest_config is None:
        _clear_active_config_state(app)
        logger.info("Config not loaded")
    else:
        run_config = RunConfig.model_validate(json.loads(latest_config.config_json))
        _set_active_config_state(
            app,
            run_config=run_config,
            version_id=latest_config.id,
            source=latest_config.source,
        )
        logger.info("Loaded run config from SQLite config_updates table")
    yield


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if _is_api_docs_enabled() else None,
    redoc_url="/redoc" if _is_api_docs_enabled() else None,
    openapi_url="/openapi.json" if _is_api_docs_enabled() else None,
)
configure_logfire(app)
app.include_router(ui_router)


@app.get("/config", dependencies=[Depends(require_ui_basic_auth)])
async def get_config():
    if app.state.run_config is None or app.state.run_config_version_id is None:
        raise HTTPException(status_code=404, detail="Config not initialized")
    return {
        "id": app.state.run_config_version_id,
        "config": app.state.run_config.model_dump(mode="json"),
    }


@app.put("/config", dependencies=[Depends(require_ui_basic_auth)])
async def update_config(payload: ConfigUpdateRequest):
    try:
        run_config = payload.config
        config_document = run_config.model_dump(mode="json")
        record = await app.state.config_repository.insert(config=config_document, source=payload.source)
        _set_active_config_state(
            app,
            run_config=run_config,
            version_id=record.id,
            source=record.source,
        )
        return {"id": record.id, "config": config_document}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config: {exc}") from None


@app.post("/analyse-video", dependencies=[Depends(require_vid_analyser_api_key)])
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
    run_config, config_version_id, config_source = _snapshot_active_run_config()
    request_id = uuid.uuid4().hex
    task = asyncio.create_task(
        _background_analyse_video(
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
    _track_background_task(task)
    return {"status": "accepted", "request_id": request_id}


@app.post("/analyse-video/shared", dependencies=[Depends(require_vid_analyser_api_key)])
async def analyse_shared_video(payload: AnalyseSharedVideoRequest, request: Request):
    logger.info("Received analyse-video/shared request from %s", request.client.host if request.client else "unknown")

    shared_video_path = _resolve_shared_video_path(payload.shared_video_path)
    file_size = shared_video_path.stat().st_size
    content_type = payload.content_type or mimetypes.guess_type(shared_video_path.name)[0] or "video/mp4"

    logger.info(
        "Using shared video path=%s content_type=%s size_bytes=%s metadata=%s",
        shared_video_path,
        content_type,
        file_size,
        payload.model_dump(exclude={"shared_video_path", "content_type"}),
    )
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Shared video is empty")

    run_config, config_version_id, config_source = _snapshot_active_run_config()
    request_id = uuid.uuid4().hex
    task = asyncio.create_task(
        _background_analyse_video(
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
    _track_background_task(task)
    return {"status": "accepted", "request_id": request_id}
