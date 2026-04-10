import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from pydantic import PositiveInt
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from vid_analyser.agent.memory import GLOBAL_AGENT_MEMORY_NAME, build_memory_instructions
from vid_analyser.agent.retry import create_google_retry_model
from vid_analyser.api.runtime import get_app_state, require_active_run_config, set_active_config_state
from vid_analyser.config_schema import RunConfig
from vid_analyser.db import Database, TelegramChatMessageRecord
from vid_analyser.notifications.telegram import TelegramNotificationService

DEFAULT_SYS_PROMPT = """
You are the ArgusAI assistant inside a Telegram chat used for doorbell and security-camera notifications.

You are a member of this Telegram chat and you are the same assistant that sends notifications into this chat when video doorbell clips are analysed. Notifications are not sent by some separate external operator. You send them.

Each notification is based on a video analysis result produced from a clip. In this operator role, you can inspect previous video analyses and previous sent notifications in order to answer questions, explain past behaviour, and adjust notification behaviour.

You can:
- inspect video analysis history
- inspect sent notification history
- update the global agent memory scratchpad when useful (use this to store key facts that might come in useful at a later date)
- update only notifier_style when the user clearly asks
- send a plain text response back to the Telegram chat

Response style guidance:
- the current notifier_style describes how user-facing messages should be written
- unless the user is asking to change notification style, write your reply in the current notifier style
- if the user asks to change notification style, update notifier_style first and then reply in the new style
- if you change notifier_style, say so clearly in your reply

Rules:
- base answers on tool results and provided context; do not invent system state
- agent memory is a scratchpad, not ground truth; keep it concise and useful
- every turn must end with exactly one response sent back to the Telegram chat
"""

MEMORY_CHAR_LIMIT = 200
DEFAULT_TRANSCRIPT_LIMIT = 20


@dataclass
class Deps:
    app: FastAPI
    chat_id: str
    chat_type: str | None
    sender_user_id: str | None
    sender_username: str | None
    sender_display_name: str | None
    incoming_message_id: str | None
    current_config: RunConfig
    db: Database
    telegram_service: TelegramNotificationService


async def send_telegram_reply(ctx: RunContext[Deps], text: str) -> str:
    """Send a plain text reply back to the current Telegram chat."""
    sent_message = await ctx.deps.telegram_service.send_message(chat_id=ctx.deps.chat_id, text=text)
    await ctx.deps.db.insert_telegram_chat_message(
        chat_id=ctx.deps.chat_id,
        chat_type=ctx.deps.chat_type,
        message_id=str(sent_message.message_id),
        direction="outbound",
        text=text,
    )
    return text


telegram_operator_agent = Agent(
    model=create_google_retry_model("gemini-3.1-pro-preview"),
    deps_type=Deps,
    output_type=[send_telegram_reply],
)


def _build_message_history(records: list[TelegramChatMessageRecord]) -> list[ModelRequest | ModelResponse]:
    ordered_records = list(reversed(records))
    message_history: list[ModelRequest | ModelResponse] = []
    for record in ordered_records:
        if record.direction == "inbound":
            sender = record.sender_display_name or record.sender_username or record.sender_user_id or "Unknown"
            message_history.append(
                ModelRequest(parts=[UserPromptPart(content=f"{record.created_at} {sender}: {record.text}")])
            )
            continue
        if record.direction == "outbound":
            message_history.append(
                ModelResponse(parts=[TextPart(content=f"{record.created_at} ArgusAI Bot: {record.text}")])
            )
    return message_history


def _format_current_user_message(
    *,
    sender_display_name: str | None,
    sender_username: str | None,
    sender_user_id: str | None,
    message_text: str,
) -> str:
    sender = sender_display_name or sender_username or sender_user_id or "Unknown"
    return f"{sender}: {message_text}"


def _summarize_analysis_json(result_json: str) -> str:
    try:
        parsed = json.loads(result_json)
    except json.JSONDecodeError:
        return result_json[:300]
    if isinstance(parsed, dict):
        interesting = {
            "parking_spot_status": parsed.get("parking_spot_status"),
            "number_plate": parsed.get("number_plate"),
            "events_description": parsed.get("events_description"),
            "ir_mode": parsed.get("ir_mode"),
        }
        return json.dumps(interesting, ensure_ascii=True)
    return result_json[:300]


def _serialize_record(record: object) -> dict[str, object]:
    serialized: dict[str, object] = {}
    for field_name, value in vars(record).items():
        if field_name.startswith("_"):
            continue
        if isinstance(value, datetime):
            serialized[field_name] = value.isoformat()
        else:
            serialized[field_name] = value
    return serialized


@telegram_operator_agent.instructions
def get_base_instructions(_: RunContext[Deps]) -> str:
    return DEFAULT_SYS_PROMPT


@telegram_operator_agent.instructions
async def get_sender_context(ctx: RunContext[Deps]) -> str:
    return "\n".join(
        [
            f"Current timestamp: {datetime.now(UTC).isoformat()}",
            f"Telegram chat id: {ctx.deps.chat_id}",
            f"Telegram chat type: {ctx.deps.chat_type or 'unknown'}",
            f"Current sender user id: {ctx.deps.sender_user_id or 'unknown'}",
            f"Current sender username: {ctx.deps.sender_username or 'unknown'}",
            f"Current sender display name: {ctx.deps.sender_display_name or 'unknown'}",
        ]
    )


@telegram_operator_agent.instructions
async def get_config_context(ctx: RunContext[Deps]) -> str:
    return (
        "Current notifier style guidance for user-facing messages:\n"
        f"{ctx.deps.current_config.notifier_style or 'No notifier style is configured.'}"
    )


@telegram_operator_agent.instructions
async def get_operator_prompt(ctx: RunContext[Deps]) -> str:
    return ctx.deps.current_config.telegram_operator_sys_prompt or ""


@telegram_operator_agent.instructions
async def inject_memory_context(ctx: RunContext[Deps]) -> str | None:
    return await build_memory_instructions(
        db=ctx.deps.db,
        limit=ctx.deps.current_config.agent_memory_limit,
        decay_days=ctx.deps.current_config.agent_memory_half_life_days,
    )


@telegram_operator_agent.tool
async def query_vid_analysis_results(
    ctx: RunContext[Deps],
    date_from: str | None = None,
    date_to: str | None = None,
    keyword: str | None = None,
    limit: PositiveInt = 10,
) -> list[dict[str, Any]]:
    """Query recent video analysis results by optional ISO timestamp bounds and keyword."""
    records = await ctx.deps.db.query_analyses(
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
        limit=limit,
    )
    return [_serialize_record(record) for record in records]


@telegram_operator_agent.tool
async def query_sent_notifications(
    ctx: RunContext[Deps],
    date_from: str | None = None,
    date_to: str | None = None,
    keyword: str | None = None,
    limit: PositiveInt = 10,
) -> list[dict[str, Any]]:
    """Query sent notifications for the current Telegram chat by optional ISO timestamp bounds and keyword."""
    records = await ctx.deps.db.query_notifications(
        chat_id=ctx.deps.chat_id,
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
        limit=limit,
    )
    return [_serialize_record(record) for record in records]


@telegram_operator_agent.tool(
    description=(
        f"Store a new append-only global memory item. "
        f"Provide memory_text plus a weight and whether it is a core memory. "
        f"Non-core memories fade with time according to the weight they are given; "
        f"higher weights stay relevant longer, and 1 is the strongest non-core weight. "
        f"Core memories do not fade with time. "
        f"Keep memory_text concise and under the size limit of {MEMORY_CHAR_LIMIT}."
    )
)
async def replace_agent_memory(
    ctx: RunContext[Deps],
    memory_text: str,
    weight: float = 1.0,
    is_core: bool = False,
) -> str:
    normalized = memory_text.strip()
    if len(normalized) > MEMORY_CHAR_LIMIT:
        raise ModelRetry(f"memory_text exceeds {MEMORY_CHAR_LIMIT} characters")
    if weight <= 0 or weight > 1:
        raise ModelRetry("weight must be > 0 and <= 1")
    await ctx.deps.db.insert_agent_memory(
        agent_name=GLOBAL_AGENT_MEMORY_NAME,
        memory_text=normalized,
        weight=weight,
        is_core=is_core,
    )
    return f"Stored new global memory item weight={weight} core={is_core} ({len(normalized)} chars)."


@telegram_operator_agent.tool
async def update_notifier_style(ctx: RunContext[Deps], notifier_style: str) -> str:
    """Update only the notifier_style field in the persisted run config."""
    updated_config = ctx.deps.current_config.model_copy(update={"notifier_style": notifier_style})
    config_document = updated_config.model_dump(mode="json")
    record = await ctx.deps.db.insert_config(config=config_document, source="telegram-operator")
    set_active_config_state(
        ctx.deps.app,
        run_config=updated_config,
        version_id=record.id,
        source=record.source,
    )
    ctx.deps.current_config = updated_config
    return f"Updated notifier_style to: {notifier_style}"


async def run_telegram_operator_agent(
    *,
    app: FastAPI,
    chat_id: str,
    chat_type: str | None,
    sender_user_id: str | None,
    sender_username: str | None,
    sender_display_name: str | None,
    incoming_message_id: str | None,
    message_text: str,
) -> str:
    state = get_app_state(app)
    current_config = require_active_run_config(app)
    deps = Deps(
        app=app,
        chat_id=chat_id,
        chat_type=chat_type,
        sender_user_id=sender_user_id,
        sender_username=sender_username,
        sender_display_name=sender_display_name,
        incoming_message_id=incoming_message_id,
        current_config=current_config,
        db=state.db,
        telegram_service=TelegramNotificationService(),
    )
    records = await state.db.get_recent_telegram_chat_messages(
        chat_id=chat_id,
        limit=DEFAULT_TRANSCRIPT_LIMIT,
    )
    current_user_message = _format_current_user_message(
        sender_display_name=sender_display_name,
        sender_username=sender_username,
        sender_user_id=sender_user_id,
        message_text=message_text,
    )
    result = await telegram_operator_agent.run(
        current_user_message,
        deps=deps,
        message_history=_build_message_history(records[1:]) if records else None,
    )
    return result.output
