import json
from typing import Any, Callable

from pydantic import BaseModel

from vid_analyser.db import ExecutionRepository

BOOKINGS_S3_BUCKET = "jp-bookings"
BOOKINGS_S3_KEY = "bookings.json"
TIME_TOKEN = "{{time}}"
BOOKINGS_TOKEN = "{{bookings}}"
PREVIOUS_MESSAGES_TOKEN = "{{previous_messages}}"


def build_user_prompt(
    *,
    metadata: BaseModel,
    template: str,
    load_json_document: Callable[[str, str], dict[str, Any]],
    execution_repository: ExecutionRepository,
    previous_messages_limit: int = 10,
) -> str:
    return _build_prompt(
        metadata=metadata,
        template=template,
        append_metadata_when_static=True,
        load_json_document=load_json_document,
        execution_repository=execution_repository,
        previous_messages_limit=previous_messages_limit,
    )


def build_system_prompt(
    *,
    metadata: BaseModel,
    template: str,
    load_json_document: Callable[[str, str], dict[str, Any]],
    execution_repository: ExecutionRepository,
    previous_messages_limit: int = 10,
) -> str:
    return _build_prompt(
        metadata=metadata,
        template=template,
        append_metadata_when_static=False,
        load_json_document=load_json_document,
        execution_repository=execution_repository,
        previous_messages_limit=previous_messages_limit,
    )


def _build_prompt(
    *,
    metadata: BaseModel,
    template: str,
    append_metadata_when_static: bool,
    load_json_document: Callable[[str, str], dict[str, Any]],
    execution_repository: ExecutionRepository,
    previous_messages_limit: int,
) -> str:
    has_time_token = TIME_TOKEN in template
    has_bookings_token = BOOKINGS_TOKEN in template
    has_previous_messages_token = PREVIOUS_MESSAGES_TOKEN in template

    if not any((has_time_token, has_bookings_token, has_previous_messages_token)):
        if append_metadata_when_static:
            return _build_fallback_user_prompt(metadata=metadata, base_prompt=template)
        return template

    replacements: dict[str, str] = {}
    metadata_dict = metadata.model_dump(exclude_none=True)
    if has_time_token:
        replacements[TIME_TOKEN] = (
            metadata_dict.get("start_time")
            or metadata_dict.get("received_at")
            or "unknown"
        )
    if has_bookings_token:
        bookings_document = load_json_document(BOOKINGS_S3_BUCKET, BOOKINGS_S3_KEY)
        replacements[BOOKINGS_TOKEN] = _format_bookings_text(bookings_document)
    if has_previous_messages_token:
        if previous_messages_limit > 0:
            messages = execution_repository.get_recent_notification_messages(limit=previous_messages_limit)
            replacements[PREVIOUS_MESSAGES_TOKEN] = _format_previous_messages_text(messages)
        else:
            replacements[PREVIOUS_MESSAGES_TOKEN] = "None."

    return _render_prompt_template(template, replacements)


def _build_fallback_user_prompt(*, metadata: BaseModel, base_prompt: str) -> str:
    metadata_dict = metadata.model_dump(exclude_none=True)

    lines = [base_prompt]
    if not metadata_dict:
        return base_prompt

    lines.extend(["", "Event metadata:"])
    for key, value in metadata_dict.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _format_bookings_text(bookings_document: dict | list | str) -> str:
    if isinstance(bookings_document, str):
        return bookings_document
    return json.dumps(bookings_document, indent=2, sort_keys=True)


def _format_previous_messages_text(messages: list[dict[str, str | None]]) -> str:
    if not messages:
        return "None."
    lines = [f"The previous {len(messages)} messages were as follows:", ""]
    for item in messages:
        lines.append(f"start_time: {item['start_time'] or 'unknown'}")
        lines.append(f"message: {item['message_for_user'] or ''}")
        lines.append("")
    return "\n".join(lines).strip()


def _render_prompt_template(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered
