import logging
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from starlette.datastructures import UploadFile

from vid_analyser.pipeline import RunConfig, run

logger = logging.getLogger(__name__)

RUN_CONFIG_ENV_VAR = "VID_ANALYSER_RUN_CONFIG_PATH"
MAX_PART_SIZE_BYTES = 100 * 1024 * 1024


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    config_path = os.getenv(RUN_CONFIG_ENV_VAR)
    if not config_path:
        raise RuntimeError(f"{RUN_CONFIG_ENV_VAR} is not set")

    app.state.run_config = RunConfig.from_json_path(config_path)
    logger.info("Loaded run config from %s", config_path)
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/analyse-video")
async def analyse_video(
    request: Request,
):
    logger.info("Received analyse-video request from %s", request.client.host if request.client else "unknown")
    form = await request.form(max_part_size=MAX_PART_SIZE_BYTES)
    video = (
        form.get("video")
        or form.get("file")
        or form.get("binary")
        or form.get("data")
    )
    user_prompt = form.get("user_prompt")
    system_prompt = form.get("system_prompt")

    if not isinstance(video, UploadFile):
        field_names = list(form.keys())
        logger.warning("Missing/invalid video field. Received form fields: %s", field_names)
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing or invalid video upload. Send the binary file field as "
                "'video', 'file', 'binary', or 'data'."
            ),
        )
    if not isinstance(user_prompt, str) or not isinstance(system_prompt, str):
        logger.warning("Missing prompt fields. Received form fields: %s", list(form.keys()))
        raise HTTPException(status_code=400, detail="Missing user_prompt/system_prompt")

    video_bytes = await video.read()
    file_size = len(video_bytes)
    logger.info(
        "Parsed upload filename=%s content_type=%s size_bytes=%s prompt_lengths user=%s system=%s",
        video.filename,
        video.content_type,
        file_size,
        len(user_prompt),
        len(system_prompt),
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
            video_path=temp_path,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            config=app.state.run_config,
        )
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
