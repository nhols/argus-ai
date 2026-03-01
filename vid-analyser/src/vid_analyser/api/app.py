import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from vid_analyser.pipeline import RunConfig, run

logger = logging.getLogger(__name__)

RUN_CONFIG_ENV_VAR = "VID_ANALYSER_RUN_CONFIG_PATH"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_path = os.getenv(RUN_CONFIG_ENV_VAR)
    if not config_path:
        raise RuntimeError(f"{RUN_CONFIG_ENV_VAR} is not set")

    app.state.run_config = RunConfig.from_json_path(config_path)
    logger.info("Loaded run config from %s", config_path)
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/analyse-video")
async def analyse_video(
    video: UploadFile = File(...),
    user_prompt: str = Form(...),
    system_prompt: str = Form(...),
):
    video_bytes = await video.read()
    file_size = len(video_bytes)
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    start = time.perf_counter()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
            tmp_file.write(video_bytes)
            temp_path = Path(tmp_file.name)

        response = await run(
            video_path=temp_path,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            config=app.state.run_config,
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info("Analysed video size_bytes=%s duration_ms=%.2f", file_size, duration_ms)
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
