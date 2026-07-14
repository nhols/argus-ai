import asyncio
import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import boto3
from fastapi import FastAPI

from vid_analyser.agent.models import ParkingSpotAssessment, ParkingSpotStatus
from vid_analyser.db import Database, VidAnalysisRecord
from vid_analyser.schedule import run_hourly_schedule

logger = logging.getLogger(__name__)

PARKING_AGENT_LIVE_AT = datetime(2026, 7, 5, 11, 9, 9, tzinfo=UTC)
DEFAULT_R2_PREFIX = "parking-observations/v1"


@dataclass(frozen=True)
class ParkingFeedSettings:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    timezone: ZoneInfo
    prefix: str = DEFAULT_R2_PREFIX

    @classmethod
    def from_env(cls) -> "ParkingFeedSettings | None":
        values = {
            "endpoint_url": os.getenv("PARKING_FEED_R2_ENDPOINT_URL", "").strip(),
            "access_key_id": os.getenv("PARKING_FEED_R2_ACCESS_KEY_ID", "").strip(),
            "secret_access_key": os.getenv("PARKING_FEED_R2_SECRET_ACCESS_KEY", "").strip(),
            "bucket": os.getenv("PARKING_FEED_R2_BUCKET", "").strip(),
        }
        if not any(values.values()):
            return None
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError("Incomplete parking feed R2 configuration: " + ", ".join(missing))
        timezone_name = os.getenv("PARKING_FEED_TIMEZONE", "").strip()
        if not timezone_name:
            raise RuntimeError("Incomplete parking feed R2 configuration: timezone")
        prefix = os.getenv("PARKING_FEED_R2_PREFIX", DEFAULT_R2_PREFIX).strip("/") or DEFAULT_R2_PREFIX
        return cls(**values, timezone=ZoneInfo(timezone_name), prefix=prefix)


@dataclass(frozen=True)
class ParkingObservation:
    id: int
    observed_at: datetime
    status: ParkingSpotStatus
    plate: str | None
    vehicle_description: str | None
    timezone: ZoneInfo

    def month(self) -> str:
        return self.observed_at.astimezone(self.timezone).strftime("%Y-%m")


def normalize_clip_start(value: datetime, timezone: ZoneInfo) -> datetime:
    """Interpret Eufy's timestamp as wall-clock time in the configured timezone."""
    wall_time = value.replace(tzinfo=None, fold=0).replace(tzinfo=timezone)
    return wall_time.astimezone(UTC)


def parking_observation_from_record(
    record: VidAnalysisRecord,
    timezone: ZoneInfo,
) -> ParkingObservation:
    if record.clip_start_time is None:
        raise ValueError(f"Analysis {record.id} has no clip start time")
    assessment = ParkingSpotAssessment.model_validate_json(record.result_json)
    return ParkingObservation(
        id=record.id,
        observed_at=normalize_clip_start(record.clip_start_time, timezone),
        status=assessment.parking_spot_status,
        plate=assessment.number_plate,
        vehicle_description=assessment.vehicle_description,
        timezone=timezone,
    )


def _serialize_observation(observation: ParkingObservation) -> dict[str, Any]:
    return {
        "id": observation.id,
        "observedAt": observation.observed_at.isoformat().replace("+00:00", "Z"),
        "status": observation.status,
        "plate": observation.plate,
        "vehicleDescription": observation.vehicle_description,
    }


def _r2_client(settings: ParkingFeedSettings):
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        region_name="auto",
    )


async def publish_parking_feed(
    db: Database,
    settings: ParkingFeedSettings,
    *,
    client: Any | None = None,
    now: datetime | None = None,
) -> int:
    launch_timestamp = PARKING_AGENT_LIVE_AT.isoformat()
    pending = await db.query_unpushed_parking_feed_analyses(created_from=launch_timestamp)
    if not pending:
        logger.info("No new parking observations to publish")
        return 0

    pending_observations = [
        parking_observation_from_record(record, settings.timezone)
        for record in pending
    ]
    dirty_months = {observation.month() for observation in pending_observations}
    all_records = await db.query_analyses(
        date_from=launch_timestamp,
        limit=None,
    )
    observations_by_month: dict[str, list[ParkingObservation]] = defaultdict(list)
    for record in all_records:
        if record.clip_start_time is None:
            continue
        observation = parking_observation_from_record(record, settings.timezone)
        month = observation.month()
        if month in dirty_months:
            observations_by_month[month].append(observation)

    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    generated_at_text = generated_at.isoformat().replace("+00:00", "Z")
    r2 = client or _r2_client(settings)
    for month in sorted(dirty_months):
        observations = sorted(
            observations_by_month[month],
            key=lambda observation: (observation.observed_at, observation.id),
        )
        body = json.dumps(
            {
                "schemaVersion": 1,
                "month": month,
                "generatedAt": generated_at_text,
                "observations": [
                    _serialize_observation(observation)
                    for observation in observations
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        key = f"{settings.prefix}/{month}.json"
        await asyncio.to_thread(
            r2.put_object,
            Bucket=settings.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            CacheControl="no-store",
        )
        logger.info(
            "Published %s parking observations to r2://%s/%s",
            len(observations),
            settings.bucket,
            key,
        )

    await db.mark_parking_feed_analyses_pushed(
        [record.id for record in pending],
        pushed_at=generated_at.isoformat(),
    )
    return len(pending)


async def run_parking_feed_schedule(app: FastAPI) -> None:
    async def publish(scheduled_for: datetime) -> int:
        settings = ParkingFeedSettings.from_env()
        if settings is None:
            logger.info("Parking feed R2 is not configured; skipping hourly publish")
            return 0
        return await publish_parking_feed(
            app.state.db,
            settings,
            now=scheduled_for,
        )

    await run_hourly_schedule(publish, job_name="parking feed publish")
