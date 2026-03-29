import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, NonNegativeInt
from pydantic_ai import Agent, RunContext
from vid_analyser.agent.utils import get_timestamps
from vid_analyser.notifications.base import NotificationService

logger = logging.getLogger()

DEFAULT_SYS_PROMT = """
You decide whether a security-camera video warrants notifying the user.

Base your decision only on what is visible in the video and any supplied context. Do not invent facts or overstate uncertainty.

If the clip contains a relevant event, produce a short, clear notification message for the user. The message should:
- state what happened in plain language
- mention timing or other context only when it is useful
- stay concise and neutral in tone
- avoid internal labels, JSON-style wording, or speculation

If there is no meaningful event, the activity is routine, or the evidence is too weak to justify bothering the user, choose `NoNotification` and briefly explain why.
"""


@dataclass
class Deps:
    # dbconn: ... TODO to get message histroy from db
    video_path: Path
    system_prompt: str | None
    video_start_time: datetime
    notification_service: NotificationService
    chat_id: str | None
    get_bookings: bool
    n_previous_messages: NonNegativeInt


async def send_notification(ctx: RunContext[Deps], message: str) -> str:
    """This will send `message` to the user, make sure to follow any style guidance given."""
    if ctx.deps.chat_id is None:
        logger.info("`chat_id` is not configured, notification will not be sent")
        return message
    await ctx.deps.notification_service.send_video(
        chat_id=ctx.deps.chat_id,
        video_path=ctx.deps.video_path,
        caption=message,
    )
    # TODO write message to db
    return message


class NoNotification(BaseModel):
    """Use this when a message should not be sent to the user."""

    explanation: str


notifier_agent = Agent(
    model="google-gla:gemini-3.1-pro-preview",
    deps_type=Deps,
    output_type=[send_notification, NoNotification],
)


@notifier_agent.instructions
async def set_timestamps(ctx: RunContext[Deps]) -> str:
    return get_timestamps(ctx.deps.video_start_time)


@notifier_agent.instructions
async def get_system_prompt(ctx: RunContext[Deps]) -> str:
    return ctx.deps.system_prompt or DEFAULT_SYS_PROMT


@notifier_agent.instructions
def get_bookings(ctx: RunContext[Deps]) -> str | None:
    if not ctx.deps.get_bookings:
        return None


@notifier_agent.instructions
def get_previous_messages(ctx: RunContext[Deps]) -> str | None:
    if ctx.deps.n_previous_messages == 0:
        return None
    msgs = []  # TODO fetch from db
