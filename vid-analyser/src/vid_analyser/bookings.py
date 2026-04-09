import json
import logging
import os
from datetime import datetime
from typing import Any

import boto3

logger = logging.getLogger(__name__)

BOOKINGS_S3_BUCKET_ENV_VAR = "BOOKINGS_S3_BUCKET"
BOOKINGS_S3_KEY_ENV_VAR = "BOOKINGS_S3_KEY"


def load_bookings_json() -> dict[str, Any] | list[Any] | None:
    bucket = os.getenv(BOOKINGS_S3_BUCKET_ENV_VAR)
    key = os.getenv(BOOKINGS_S3_KEY_ENV_VAR)
    if not bucket or not key:
        logger.warning(
            "Bookings requested but S3 location is not configured: %s=%r %s=%r",
            BOOKINGS_S3_BUCKET_ENV_VAR,
            bucket,
            BOOKINGS_S3_KEY_ENV_VAR,
            key,
        )
        return None

    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Bookings S3 object is not valid JSON bucket=%s key=%s", bucket, key)
        return None


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_local_text(value: str) -> str:
    return _parse_datetime(value).strftime("%d/%m/%Y, %H:%M:%S")


def _filter_todays_bookings(bookings: dict[str, Any] | list[Any], *, now: datetime) -> list[dict[str, Any]]:
    items = bookings.get("items", []) if isinstance(bookings, dict) else bookings
    simplified: list[dict[str, Any]] = []

    for booking in items:
        if booking.get("status") == "cancelled":
            continue

        start_date = booking.get("start_date")
        end_date = booking.get("end_date")
        if not start_date or not end_date:
            continue
        start_at = _parse_datetime(start_date)
        end_at = _parse_datetime(end_date)
        if not (start_at.date() <= now.date() <= end_at.date()):
            continue

        vehicle = booking.get("vehicle", {}).get("data", {})
        driver = booking.get("driver", {}).get("data", {})
        simplified.append(
            {
                "driver_name": driver.get("name"),
                "start_time": _to_local_text(start_date),
                "end_time": _to_local_text(end_date),
                "vehicle_make": vehicle.get("make"),
                "vehicle_model": vehicle.get("model"),
                "vehicle_colour": vehicle.get("colour"),
                "vehicle_registration": vehicle.get("registration"),
            }
        )

    return simplified


def format_bookings_prompt(bookings: dict[str, Any] | list[Any] | None, *, now: datetime) -> str | None:
    if bookings is None:
        return None
    simplified = _filter_todays_bookings(bookings, now=now)
    if not simplified:
        return "Bookings for today:\nthere are no bookings"
    return "Bookings for today:\n" + json.dumps(simplified, indent=2)
