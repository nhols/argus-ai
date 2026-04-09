import asyncio
import json
import logging
import os
import sys

from fastapi import FastAPI, HTTPException

from vid_analyser.config_schema import RunConfig
from vid_analyser.db import ConfigUpdateRepository, SentNotificationRepository, VidAnalysisRepository, init_database
from vid_analyser.storage import build_storage_provider

logger = logging.getLogger(__name__)

SQLITE_PATH_ENV_VAR = "VID_ANALYSER_SQLITE_PATH"
DEFAULT_SQLITE_PATH = "/app/data/vid_analyser.db"
MAX_CONCURRENT_JOBS_ENV_VAR = "VID_ANALYSER_MAX_CONCURRENT_JOBS"


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def config_summary(config: RunConfig | None) -> dict[str, object]:
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


def get_max_concurrent_jobs() -> int:
    raw = os.getenv(MAX_CONCURRENT_JOBS_ENV_VAR, "1").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{MAX_CONCURRENT_JOBS_ENV_VAR} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise RuntimeError(f"{MAX_CONCURRENT_JOBS_ENV_VAR} must be >= 1, got {value}")
    return value


def clear_active_config_state(app: FastAPI) -> None:
    app.state.run_config = None
    app.state.run_config_version_id = None
    app.state.run_config_source = None


def set_active_config_state(
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
        config_summary(run_config),
    )


def require_active_run_config(app: FastAPI) -> RunConfig:
    run_config = getattr(app.state, "run_config", None)
    if run_config is None:
        raise HTTPException(status_code=503, detail="Config not initialized")
    logger.info(
        "Using active run config id=%s source=%s summary=%s",
        getattr(app.state, "run_config_version_id", None),
        getattr(app.state, "run_config_source", None),
        config_summary(run_config),
    )
    return run_config


async def initialize_app_state(app: FastAPI) -> None:
    configure_logging()
    db_path = os.getenv(SQLITE_PATH_ENV_VAR, DEFAULT_SQLITE_PATH)
    session_factory = await init_database(db_path)
    app.state.config_repository = ConfigUpdateRepository(session_factory)
    app.state.notification_repository = SentNotificationRepository(session_factory)
    app.state.analysis_repository = VidAnalysisRepository(session_factory)
    app.state.storage_provider = build_storage_provider()
    app.state.background_tasks = set()
    app.state.local_video_cleanup_lock = asyncio.Lock()
    app.state.max_concurrent_jobs = get_max_concurrent_jobs()
    app.state.analysis_semaphore = asyncio.Semaphore(app.state.max_concurrent_jobs)
    logger.info("Configured max concurrent analysis jobs=%s", app.state.max_concurrent_jobs)
    latest_config = await app.state.config_repository.get_latest()
    if latest_config is None:
        clear_active_config_state(app)
        logger.info("Config not loaded")
    else:
        run_config = RunConfig.model_validate(json.loads(latest_config.config_json))
        set_active_config_state(
            app,
            run_config=run_config,
            version_id=latest_config.id,
            source=latest_config.source,
        )
        logger.info("Loaded run config from SQLite config_updates table")
