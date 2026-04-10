import json
import logging
import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from vid_analyser.agent.telegram_operator import run_telegram_operator_agent
from vid_analyser.api.runtime import get_app_state, require_active_run_config

logger = logging.getLogger(__name__)

TELEGRAM_WEBHOOK_PATH_SECRET_ENV_VAR = "TELEGRAM_WEBHOOK_PATH_SECRET"
TELEGRAM_WEBHOOK_HEADER_SECRET_ENV_VAR = "TELEGRAM_WEBHOOK_HEADER_SECRET"


class TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    is_bot: bool
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

    @field_validator("username", "first_name", "last_name", mode="after")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @property
    def display_name(self) -> str | None:
        parts = [part for part in [self.first_name, self.last_name] if part]
        if parts:
            return " ".join(parts)
        if self.username:
            return f"@{self.username}"
        return None


class TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    type: str

    @field_validator("type", mode="after")
    @classmethod
    def strip_type(cls, value: str) -> str:
        return value.strip()


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message_id: int
    chat: TelegramChat
    from_: TelegramUser | None = Field(default=None, alias="from")
    text: str

    @field_validator("text", mode="after")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int
    message: TelegramMessage

    @property
    def description(self) -> dict[str, object]:
        return {
            "update_id": self.update_id,
            "kind": "message",
            "message_id": self.message.message_id,
            "chat_id": self.message.chat.id,
            "chat_type": self.message.chat.type,
            "from_id": self.message.from_.id if self.message.from_ is not None else None,
            "text": self.message.text,
        }

    @property
    def chat_id_str(self) -> str:
        return str(self.message.chat.id)

    @property
    def message_id_str(self) -> str:
        return str(self.message.message_id)

    @property
    def update_id_str(self) -> str:
        return str(self.update_id)

    @property
    def sender_user_id_str(self) -> str | None:
        if self.message.from_ is None:
            return None
        return str(self.message.from_.id)

    @property
    def sender_username(self) -> str | None:
        if self.message.from_ is None:
            return None
        return self.message.from_.username

    @property
    def sender_display_name(self) -> str | None:
        if self.message.from_ is None:
            return None
        return self.message.from_.display_name


def _get_required_env_var(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise HTTPException(status_code=503, detail=f"{name} is not configured")
    return value


def _verify_telegram_webhook(path_secret: str, header_secret: str | None) -> None:
    expected_path_secret = _get_required_env_var(TELEGRAM_WEBHOOK_PATH_SECRET_ENV_VAR)
    expected_header_secret = _get_required_env_var(TELEGRAM_WEBHOOK_HEADER_SECRET_ENV_VAR)

    if not secrets.compare_digest(path_secret, expected_path_secret):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook path secret")
    if header_secret is None or not secrets.compare_digest(header_secret, expected_header_secret):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret header")


def _describe_telegram_update(payload: dict[str, object], update: TelegramUpdate | None = None) -> dict[str, object]:
    if update is None:
        return {"update_id": payload.get("update_id"), "kind": "unsupported"}
    return update.description


def _parse_telegram_update(payload: dict[str, object]) -> TelegramUpdate | None:
    try:
        return TelegramUpdate.model_validate(payload)
    except ValidationError:
        logger.info("Ignoring Telegram payload with unexpected shape payload=%s", payload)
        return None


async def _handle_telegram_update(request: Request, payload: dict[str, object]) -> str:
    app = request.app
    state = get_app_state(app)
    config = require_active_run_config(app)
    if not config.telegram_chat_id:
        logger.info("Ignoring Telegram update because telegram_chat_id is not configured")
        return "ignored"

    update = _parse_telegram_update(payload)
    description = _describe_telegram_update(payload, update)
    if update is None:
        logger.info("Ignoring unsupported Telegram update %s", description)
        return "ignored"

    if update.chat_id_str != config.telegram_chat_id:
        logger.info("Ignoring Telegram message from non-configured chat %s", description)
        return "ignored"

    if not update.message.text:
        logger.info("Ignoring Telegram message without text %s", description)
        return "ignored"

    if update.message.from_ is not None and update.message.from_.is_bot:
        logger.info("Ignoring Telegram bot-authored message %s", description)
        return "ignored"

    if await state.db.has_telegram_update(update_id=update.update_id_str):
        logger.info("Ignoring duplicate Telegram update_id=%s", update.update_id_str)
        return "ignored"

    await state.db.insert_telegram_chat_message(
        chat_id=update.chat_id_str,
        chat_type=update.message.chat.type,
        message_id=update.message_id_str,
        update_id=update.update_id_str,
        direction="inbound",
        sender_user_id=update.sender_user_id_str,
        sender_username=update.sender_username,
        sender_display_name=update.sender_display_name,
        text=update.message.text,
    )
    logger.info("Accepted Telegram message update %s", description)
    await run_telegram_operator_agent(
        app=app,
        chat_id=update.chat_id_str,
        chat_type=update.message.chat.type,
        sender_user_id=update.sender_user_id_str,
        sender_username=update.sender_username,
        sender_display_name=update.sender_display_name,
        incoming_message_id=update.message_id_str,
        message_text=update.message.text,
    )
    return "ok"


async def _parse_telegram_update_payload(raw_body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Telegram update payload must be a JSON object")
    return payload


router = APIRouter(prefix="/webhooks")


@router.post("/telegram/{path_secret}")
async def telegram_webhook(
    path_secret: str,
    request: Request,
    telegram_secret_token: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
):
    _verify_telegram_webhook(path_secret, telegram_secret_token)
    payload = await _parse_telegram_update_payload(await request.body())
    status = await _handle_telegram_update(request, payload)
    return {"status": status}
