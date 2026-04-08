import json
import logging
import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger(__name__)

TELEGRAM_WEBHOOK_PATH_SECRET_ENV_VAR = "TELEGRAM_WEBHOOK_PATH_SECRET"
TELEGRAM_WEBHOOK_HEADER_SECRET_ENV_VAR = "TELEGRAM_WEBHOOK_HEADER_SECRET"


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


def _describe_telegram_update(payload: dict[str, object]) -> dict[str, object]:
    message = payload.get("message")
    if not isinstance(message, dict):
        return {"update_id": payload.get("update_id"), "kind": "unsupported"}

    chat = message.get("chat")
    sender = message.get("from")
    return {
        "update_id": payload.get("update_id"),
        "kind": "message",
        "message_id": message.get("message_id"),
        "chat_id": chat.get("id") if isinstance(chat, dict) else None,
        "chat_type": chat.get("type") if isinstance(chat, dict) else None,
        "from_id": sender.get("id") if isinstance(sender, dict) else None,
        "text": message.get("text"),
    }


async def _handle_telegram_update(payload: dict[str, object]) -> None:
    description = _describe_telegram_update(payload)
    if description["kind"] != "message":
        logger.info("Ignoring unsupported Telegram update %s", description)
        return
    logger.info("Received Telegram message update %s", description)


async def _parse_telegram_update_payload(raw_body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Telegram update payload must be a JSON object")
    return payload

router = APIRouter(prefix="/webhooks")


@router.post("/telegram/{path_secret}", include_in_schema=False)
async def telegram_webhook(
    path_secret: str,
    request: Request,
    telegram_secret_token: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
):
    _verify_telegram_webhook(path_secret, telegram_secret_token)
    payload = await _parse_telegram_update_payload(await request.body())
    await _handle_telegram_update(payload)
    return {"status": "ok"}
