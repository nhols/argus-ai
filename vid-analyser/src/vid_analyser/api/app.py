import logging
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from vid_analyser.pipeline import RunConfig, run

logger = logging.getLogger(__name__)

CONFIG_S3_BUCKET_ENV_VAR = "VID_ANALYSER_CONFIG_S3_BUCKET"
CONFIG_S3_KEY_ENV_VAR = "VID_ANALYSER_CONFIG_S3_KEY"
DEFAULT_CONFIG_S3_KEY = "config/run_config.json"
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


def _load_run_config_from_s3(bucket: str, key: str) -> RunConfig:
    import boto3

    logger.info("Loading run config from s3://%s/%s", bucket, key)
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read().decode("utf-8")
    return RunConfig.from_json_text(body)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    bucket = os.getenv(CONFIG_S3_BUCKET_ENV_VAR)
    if not bucket:
        raise RuntimeError(f"{CONFIG_S3_BUCKET_ENV_VAR} is not set")

    key = os.getenv(CONFIG_S3_KEY_ENV_VAR, DEFAULT_CONFIG_S3_KEY)
    app.state.run_config = _load_run_config_from_s3(bucket, key)
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
    configured_user_prompt = app.state.run_config.user_prompt or DEFAULT_USER_PROMPT
    configured_system_prompt = app.state.run_config.system_prompt or DEFAULT_SYSTEM_PROMPT
    user_prompt = _build_user_prompt(metadata, base_prompt=configured_user_prompt)
    system_prompt = configured_system_prompt
    logger.info(
        "Parsed upload filename=%s content_type=%s size_bytes=%s metadata_keys=%s prompt_lengths user=%s system=%s",
        video.filename,
        video.content_type,
        file_size,
        sorted(metadata.model_dump(exclude_none=True).keys()),
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
