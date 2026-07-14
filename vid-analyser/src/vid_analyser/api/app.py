import asyncio
import logging
import os
from contextlib import asynccontextmanager
from contextlib import suppress

import logfire
from dotenv import load_dotenv
from fastapi import FastAPI
from vid_analyser.agent.weekly_roundup import run_weekly_roundup
from vid_analyser.api.routes import app_api_router, internal_router, webhook_router
from vid_analyser.api.runtime import initialize_app_state
from vid_analyser.api.ui import router as ui_router
from vid_analyser.genai_prices import start_price_updates, stop_price_updates
from vid_analyser.parking_feed import run_parking_feed_schedule
from vid_analyser.schedule import run_weekly_schedule

load_dotenv()

logger = logging.getLogger(__name__)

ENABLE_API_DOCS_ENV_VAR = "ENABLE_API_DOCS"


def configure_logfire(app: FastAPI) -> None:
    try:
        if not os.getenv("LOGFIRE_TOKEN"):
            logger.info("Logfire token not configured; skipping FastAPI and Pydantic AI instrumentation")
            return
        start_price_updates()
        logfire.configure()
        logfire.instrument_pydantic_ai(include_binary_content=False)
        logfire.instrument_fastapi(app)
    except Exception:
        stop_price_updates()
        logger.warning("Logfire not configured; skipping FastAPI and Pydantic AI instrumentation")


def is_api_docs_enabled() -> bool:
    raw = os.getenv(ENABLE_API_DOCS_ENV_VAR, "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await initialize_app_state(app)

    async def roundup_job(scheduled_for):
        return await run_weekly_roundup(app, period_end=scheduled_for)

    roundup_task = asyncio.create_task(
        run_weekly_schedule(roundup_job, job_name="weekly roundup"),
        name="weekly-roundup",
    )

    parking_feed_task = asyncio.create_task(
        run_parking_feed_schedule(app),
        name="parking-feed-publish",
    )
    try:
        yield
    finally:
        tasks = (roundup_task, parking_feed_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        stop_price_updates()


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if is_api_docs_enabled() else None,
    redoc_url="/redoc" if is_api_docs_enabled() else None,
    openapi_url="/openapi.json" if is_api_docs_enabled() else None,
)
configure_logfire(app)

app.include_router(ui_router)
app.include_router(app_api_router)
app.include_router(internal_router)
app.include_router(webhook_router)
