import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import vid_analyser.db.database as database_module  # noqa: E402
from vid_analyser.db import VidAnalysisRecord, init_database  # noqa: E402
from vid_analyser.parking_feed import (  # noqa: E402
    PARKING_AGENT_LIVE_AT,
    ParkingFeedSettings,
    ParkingObservation,
    normalize_clip_start,
    parking_observation_from_record,
    publish_parking_feed,
)


class FakeR2Client:
    def __init__(self, *, fail_key: str | None = None):
        self.objects: dict[str, bytes] = {}
        self.fail_key = fail_key

    def put_object(self, **kwargs):
        if kwargs["Key"] == self.fail_key:
            raise RuntimeError("upload failed")
        self.objects[kwargs["Key"]] = kwargs["Body"]


SETTINGS = ParkingFeedSettings(
    endpoint_url="https://example.r2.cloudflarestorage.com",
    access_key_id="key",
    secret_access_key="secret",
    bucket="dashboard",
    timezone=ZoneInfo("Europe/London"),
)


def _analysis_json(status: str, plate: str | None = None) -> str:
    return json.dumps(
        {
            "parking_spot_status": status,
            "number_plate": plate,
            "vehicle_description": "a blue car" if plate else None,
        }
    )


def test_clip_start_is_interpreted_in_configured_timezone():
    assert normalize_clip_start(
        datetime.fromisoformat("2026-07-05T12:19:48+00:00"),
        SETTINGS.timezone,
    ) == datetime.fromisoformat("2026-07-05T11:19:48+00:00")
    assert normalize_clip_start(
        datetime.fromisoformat("2026-12-05T12:19:48+00:00"),
        SETTINGS.timezone,
    ) == datetime.fromisoformat("2026-12-05T12:19:48+00:00")


def test_parking_feed_timezone_comes_from_environment(monkeypatch):
    monkeypatch.setenv(
        "PARKING_FEED_R2_ENDPOINT_URL",
        "https://example.r2.cloudflarestorage.com",
    )
    monkeypatch.setenv("PARKING_FEED_R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("PARKING_FEED_R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("PARKING_FEED_R2_BUCKET", "dashboard")
    monkeypatch.setenv("PARKING_FEED_TIMEZONE", "America/New_York")

    settings = ParkingFeedSettings.from_env()

    assert settings is not None
    assert str(settings.timezone) == "America/New_York"


def test_vid_analysis_record_is_converted_to_parking_observation():
    record = VidAnalysisRecord(
        id=123,
        created_at="2026-07-31T23:05:00+00:00",
        video_path="clip.mp4",
        result_json=_analysis_json("occupied", "AB12CDE"),
        clip_start_time=datetime.fromisoformat("2026-07-31T23:59:50+00:00"),
    )

    observation = parking_observation_from_record(record, SETTINGS.timezone)

    assert isinstance(observation, ParkingObservation)
    assert observation.observed_at == datetime.fromisoformat(
        "2026-07-31T22:59:50+00:00"
    )
    assert observation.month() == "2026-07"


def test_hourly_publish_backfills_and_updates_each_dirty_london_month(
    tmp_path, monkeypatch
):
    created_times = iter(
        [
            "2026-07-05T11:00:00+00:00",
            "2026-07-31T23:05:00+00:00",
            "2026-07-31T23:08:00+00:00",
        ]
    )
    monkeypatch.setattr(database_module, "utc_now_iso", lambda: next(created_times))

    async def _run():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        before_launch = await db.insert_analysis(
            video_path="before.mp4",
            result_json=_analysis_json("vacant"),
            clip_start_time=datetime.fromisoformat("2026-07-05T11:00:00+00:00"),
        )
        july = await db.insert_analysis(
            video_path="july.mp4",
            result_json=_analysis_json("occupied", "AB12CDE"),
            clip_start_time=datetime.fromisoformat("2026-07-31T23:59:50+00:00"),
            clip_end_time=datetime.fromisoformat("2026-08-01T00:00:10+00:00"),
        )
        august = await db.insert_analysis(
            video_path="august.mp4",
            result_json=_analysis_json("car leaving"),
            clip_start_time=datetime.fromisoformat("2026-08-01T00:02:00+00:00"),
        )
        client = FakeR2Client()
        count = await publish_parking_feed(
            db,
            SETTINGS,
            client=client,
            now=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
        )
        second_count = await publish_parking_feed(db, SETTINGS, client=client)
        records = await db.query_analyses(limit=None)
        return before_launch, july, august, client, count, second_count, records

    before_launch, july, august, client, count, second_count, records = asyncio.run(
        _run()
    )

    assert count == 2
    assert second_count == 0
    assert set(client.objects) == {
        "parking-observations/v1/2026-07.json",
        "parking-observations/v1/2026-08.json",
    }

    july_payload = json.loads(
        client.objects["parking-observations/v1/2026-07.json"]
    )
    august_payload = json.loads(
        client.objects["parking-observations/v1/2026-08.json"]
    )
    assert july_payload["observations"] == [
        {
            "id": july.id,
            "observedAt": "2026-07-31T22:59:50Z",
            "status": "occupied",
            "plate": "AB12CDE",
            "vehicleDescription": "a blue car",
        }
    ]
    assert august_payload["observations"] == [
        {
            "id": august.id,
            "observedAt": "2026-07-31T23:02:00Z",
            "status": "car leaving",
            "plate": None,
            "vehicleDescription": None,
        }
    ]

    records_by_id = {record.id: record for record in records}
    assert records_by_id[before_launch.id].parking_feed_pushed_at is None
    assert records_by_id[july.id].parking_feed_pushed_at is not None
    assert records_by_id[august.id].parking_feed_pushed_at is not None


def test_failed_month_upload_leaves_observations_unpushed(tmp_path, monkeypatch):
    created_times = iter(
        [
            "2026-07-31T23:05:00+00:00",
            "2026-07-31T23:08:00+00:00",
        ]
    )
    monkeypatch.setattr(database_module, "utc_now_iso", lambda: next(created_times))

    async def _run():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        await db.insert_analysis(
            video_path="july.mp4",
            result_json=_analysis_json("occupied"),
            clip_start_time=datetime.fromisoformat("2026-07-31T23:59:50+00:00"),
        )
        await db.insert_analysis(
            video_path="august.mp4",
            result_json=_analysis_json("vacant"),
            clip_start_time=datetime.fromisoformat("2026-08-01T00:02:00+00:00"),
        )
        client = FakeR2Client(
            fail_key="parking-observations/v1/2026-08.json"
        )
        try:
            await publish_parking_feed(db, SETTINGS, client=client)
        except RuntimeError:
            pass
        return await db.query_unpushed_parking_feed_analyses(
            created_from=PARKING_AGENT_LIVE_AT.isoformat()
        )

    pending = asyncio.run(_run())
    assert len(pending) == 2
