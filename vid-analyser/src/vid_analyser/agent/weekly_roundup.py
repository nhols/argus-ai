import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from pydantic_ai import Agent, RunContext
from vid_analyser.agent.models import DEFAULT_GOOGLE_MODEL
from vid_analyser.agent.retry import create_google_retry_model
from vid_analyser.db import VidAnalysisRecord
from vid_analyser.notifications.telegram import TelegramNotificationService

logger = logging.getLogger(__name__)

ROUNDUP_TIMEZONE = ZoneInfo("Europe/London")
ROUNDUP_PERIOD = timedelta(days=7)

DEFAULT_INSTRUCTIONS = """
You write the weekly roundup for a home security-camera Telegram chat.

You will receive every stored video-analysis event from the reporting period. Write one concise, useful,
Telegram-ready message summarising the week. Lead with the overall pattern, mention genuinely notable events,
and avoid listing repetitive routine activity individually. If there were no events, say so plainly. Base the
roundup only on the supplied analyses: do not invent details, infer identities, or imply that you watched the
raw videos. Return only the message to send, keep it below 3,500 characters, and do not use Markdown tables.
"""


@dataclass
class RoundupDeps:
    system_prompt: str | None
    notifier_style: str | None


weekly_roundup_agent = Agent(
    model=create_google_retry_model(DEFAULT_GOOGLE_MODEL),
    deps_type=RoundupDeps,
)


@weekly_roundup_agent.instructions
def get_roundup_instructions(ctx: RunContext[RoundupDeps]) -> str:
    return ctx.deps.system_prompt or DEFAULT_INSTRUCTIONS


@weekly_roundup_agent.instructions
def get_notifier_style_instructions(ctx: RunContext[RoundupDeps]) -> str | None:
    if not ctx.deps.notifier_style:
        return None
    return f"Notifier style and personality for the final message:\n{ctx.deps.notifier_style}"


async def generate_weekly_roundup(
    events: list[dict[str, object]],
    *,
    system_prompt: str | None,
    notifier_style: str | None,
) -> str:
    result = await weekly_roundup_agent.run(
        json.dumps(events, ensure_ascii=False),
        deps=RoundupDeps(
            system_prompt=system_prompt,
            notifier_style=notifier_style,
        ),
    )
    return result.output


def _serialize_event(record: VidAnalysisRecord) -> dict[str, object]:
    try:
        analysis: object = json.loads(record.result_json)
    except json.JSONDecodeError:
        analysis = record.result_json
    return {
        "created_at": record.created_at,
        "analysis": analysis,
    }


async def run_weekly_roundup(app: FastAPI, *, period_end: datetime | None = None) -> str | None:
    state = app.state
    config = state.run_config
    if config is None:
        logger.info("Skipping weekly roundup because no run config is active")
        return None
    if not config.telegram_chat_id:
        logger.info("Skipping weekly roundup because no Telegram chat is configured")
        return None

    end_local = (period_end or datetime.now(UTC)).astimezone(ROUNDUP_TIMEZONE)
    start_local = end_local - ROUNDUP_PERIOD
    end = end_local.astimezone(UTC)
    start = start_local.astimezone(UTC)
    records = await state.db.query_analyses(
        date_from=start.isoformat(),
        date_to=end.isoformat(),
        limit=None,
    )
    events = [_serialize_event(record) for record in reversed(records)]
    message = await generate_weekly_roundup(
        events,
        system_prompt=config.weekly_roundup_sys_prompt,
        notifier_style=config.notifier_style,
    )

    telegram = TelegramNotificationService()
    sent_message = await telegram.send_message(chat_id=config.telegram_chat_id, text=message)
    await state.db.insert_telegram_chat_message(
        chat_id=config.telegram_chat_id,
        chat_type=None,
        message_id=str(sent_message.message_id),
        direction="outbound",
        text=message,
    )
    logger.info("Sent weekly roundup with %s events", len(events))
    return message
