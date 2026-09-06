import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, NonNegativeInt
from pydantic_ai import Agent, RunContext
from vid_analyser.agent.memory import build_memory_instructions
from vid_analyser.agent.models import DEFAULT_GOOGLE_MODEL
from vid_analyser.agent.notes import build_live_note_instructions
from vid_analyser.agent.retry import create_google_retry_model
from vid_analyser.agent.utils import get_timestamps
from vid_analyser.bookings import format_bookings_prompt, load_bookings_json
from vid_analyser.db import Database
from vid_analyser.notifications.base import NotificationService

logger = logging.getLogger()

SHORT_CAPTION_RULES = """Notification caption rules (take precedence over conflicting style or verbosity instructions):
- Write one plain, factual caption, ideally 3-8 words and at most 12 words unless essential for accuracy.
- Preserve uncertainty when the analysis is uncertain; brevity must not turn a possible match into a fact.
- Do not add a detailed explanation: the attached video provides the detail.
- Avoid internal labels, JSON-like wording, or implementation details.
"""


DEFAULT_SYS_PROMT = """
You decide whether a security-camera video warrants notifying the user.

Base your decision only on what is visible in the video and any supplied context. Do not invent facts or overstate uncertainty.

If the clip contains a relevant event, produce a short, clear notification message for the user.

If there is no meaningful event, the activity is routine, or the evidence is too weak to justify bothering the user, choose `NoNotification` and briefly explain why.
"""


@dataclass
class Deps:
    video_path: Path
    vid_analysis_id: int | None
    system_prompt: str | None
    video_start_time: datetime
    notification_service: NotificationService | None
    db: Database | None
    chat_id: str | None
    get_bookings: bool
    n_previous_messages: NonNegativeInt
    agent_memory_limit: NonNegativeInt
    agent_memory_decay_days: float


async def send_notification(ctx: RunContext[Deps], message: str) -> str:
    """Send a short, factual video caption to the user, following the notification caption rules."""
    if ctx.deps.chat_id is None or ctx.deps.notification_service is None:
        logger.info("Notification transport is not configured, notification will not be sent")
        return message
    await ctx.deps.notification_service.send_video(
        chat_id=ctx.deps.chat_id,
        video_path=ctx.deps.video_path,
        caption=message,
    )
    if ctx.deps.db is not None:
        await ctx.deps.db.insert_notification(
            video_path=ctx.deps.video_path,
            chat_id=ctx.deps.chat_id,
            vid_analysis_id=ctx.deps.vid_analysis_id,
            message=message,
        )
    return message


class NoNotification(BaseModel):
    """Use this when a message should not be sent to the user."""

    explanation: str


notifier_agent = Agent(
    model=create_google_retry_model(DEFAULT_GOOGLE_MODEL),
    deps_type=Deps,
    output_type=[send_notification, NoNotification],
)


@notifier_agent.instructions
async def set_timestamps(ctx: RunContext[Deps]) -> str:
    return get_timestamps(ctx.deps.video_start_time)


@notifier_agent.instructions
async def get_system_prompt(ctx: RunContext[Deps]) -> str:
    prompt = ctx.deps.system_prompt or DEFAULT_SYS_PROMT
    return f"{prompt}\n\n{SHORT_CAPTION_RULES}"


@notifier_agent.instructions
async def get_agent_memory(ctx: RunContext[Deps]) -> str | None:
    return await build_memory_instructions(
        db=ctx.deps.db,
        limit=ctx.deps.agent_memory_limit,
        decay_days=ctx.deps.agent_memory_decay_days,
    )


@notifier_agent.instructions
async def get_live_operator_notes(ctx: RunContext[Deps]) -> str | None:
    return await build_live_note_instructions(db=ctx.deps.db)


@notifier_agent.instructions
def get_bookings(ctx: RunContext[Deps]) -> str | None:
    if not ctx.deps.get_bookings:
        return None
    return format_bookings_prompt(load_bookings_json(), now=ctx.deps.video_start_time)


@notifier_agent.instructions
async def get_previous_messages(ctx: RunContext[Deps]) -> str | None:
    if ctx.deps.n_previous_messages == 0 or ctx.deps.db is None:
        logger.info("Previous message fetching not configured")
        return None
    msgs = await ctx.deps.db.get_recent_notifications(limit=ctx.deps.n_previous_messages)
    if not msgs:
        logger.info("No previous messages found")
        return None
    return "\n".join(
        ["Most recent notifications sent to the user:"] + [f"- {msg.created_at}: {msg.message}" for msg in msgs]
    )
