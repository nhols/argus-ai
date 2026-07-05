import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = ZoneInfo("Europe/London")
DEFAULT_WEEKDAY = 6  # Monday is 0, Sunday is 6.
DEFAULT_TIME = time(hour=22)


def next_weekly_run(
    now: datetime,
    *,
    weekday: int = DEFAULT_WEEKDAY,
    at: time = DEFAULT_TIME,
    timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> datetime:
    """Return the next configured weekly run time, strictly after ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(timezone)
    days_until_run = (weekday - local_now.weekday()) % 7
    candidate = datetime.combine(
        local_now.date() + timedelta(days=days_until_run),
        at,
        tzinfo=timezone,
    )
    if candidate <= local_now:
        candidate += timedelta(days=7)
    return candidate


async def run_weekly_schedule(
    job: Callable[[datetime], Awaitable[object]],
    *,
    job_name: str,
    weekday: int = DEFAULT_WEEKDAY,
    at: time = DEFAULT_TIME,
    timezone: ZoneInfo = DEFAULT_TIMEZONE,
) -> None:
    """Run an async job on a weekly schedule while the process is alive."""
    while True:
        scheduled_for = next_weekly_run(
            datetime.now(UTC),
            weekday=weekday,
            at=at,
            timezone=timezone,
        )
        delay = max(0.0, (scheduled_for - datetime.now(UTC)).total_seconds())
        logger.info("Next %s scheduled for %s", job_name, scheduled_for.isoformat())
        await asyncio.sleep(delay)
        try:
            await job(scheduled_for)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled job %s failed", job_name)
