import logging
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import logfire
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from vid_analyser.auth import require_ui_basic_auth, require_vid_analyser_api_key
from vid_analyser.pipeline.run import RunConfig, run
from vid_analyser.storage import build_storage_provider

load_dotenv()

logger = logging.getLogger(__name__)

SQLITE_PATH_ENV_VAR = "VID_ANALYSER_SQLITE_PATH"
TELEGRAM_BOT_TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
ENABLE_API_DOCS_ENV_VAR = "ENABLE_API_DOCS"
DEFAULT_SQLITE_PATH = "/app/data/vid_analyser.db"


def configure_logfire(app: FastAPI) -> None:
    try:
        logfire.configure()
        logfire.instrument_pydantic_ai()
        logfire.instrument_fastapi(app)
    except Exception:
        logger.warning("Logfire not configured; skipping FastAPI and Pydantic AI instrumentation")


class AnalyseVideoMetadata(BaseModel):
    received_at: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class ConfigUpdateRequest(BaseModel):
    config: dict
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    db_path = os.getenv(SQLITE_PATH_ENV_VAR, DEFAULT_SQLITE_PATH)
    # TODO init db
    app.state.storage_provider = build_storage_provider()
    app.state.run_config = RunConfig()
    if app.state.run_config is not None:
        logger.info("Loaded run config from SQLite config_versions table")
    yield


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if _is_api_docs_enabled() else None,
    redoc_url="/redoc" if _is_api_docs_enabled() else None,
    openapi_url="/openapi.json" if _is_api_docs_enabled() else None,
)
configure_logfire(app)


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
        ...  # TODO
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
    if app.state.run_config is None:
        raise HTTPException(status_code=503, detail="Config not initialized")
    metadata = AnalyseVideoMetadata(received_at=received_at, start_time=start_time, end_time=end_time)

    video_bytes = await video.read()
    file_size = len(video_bytes)
    logger.info(
        "Parsed upload filename=%s content_type=%s size_bytes=%s metadata=%s",
        video.filename,
        video.content_type,
        file_size,
        metadata.model_dump(),
    )
    if file_size == 0:
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
            video_path=temp_path, config=app.state.run_config, content_type=video.content_type or "video/mp4"
        )

        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Completed analysis filename=%s size_bytes=%s duration_ms=%.2f", video.filename, file_size, duration_ms
        )
        return response
    except HTTPException:
        raise
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception("Video analysis failed size_bytes=%s duration_ms=%.2f", file_size, duration_ms)
        raise HTTPException(status_code=500, detail="Video analysis failed") from None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
            logger.info("Deleted temp file %s", temp_path)
