import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.agent.models import VidAnalysis  # noqa: E402
from vid_analyser.config_schema import OverlayConfig, RunConfig  # noqa: E402
from vid_analyser.db import init_database  # noqa: E402
from vid_analyser.overlay import _build_svg_overlay  # noqa: E402
from vid_analyser.overlay_schema import Color, ZoneDefinition  # noqa: E402
from vid_analyser.pipeline import run as pipeline_run  # noqa: E402


class _StubVideoAnalyser:
    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return VidAnalysis(
            ir_mode=False,
            parking_spot_status="occupied",
            number_plate="HX72 LLM",
            vehicle_description="a dark blue compact SUV",
            events_description="A car occupies the user's parking spot.",
        )


class _StubNotifierAgent:
    def __init__(self):
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output="sent")


def _parking_config() -> RunConfig:
    return RunConfig(
        parking_spot_sys_prompt="custom parking prompt",
        overlay=OverlayConfig(
            zones=[
                ZoneDefinition(
                    label="Neighbour's parking spot",
                    polygon=[(0.0, 0.0), (0.4, 0.0), (0.4, 1.0)],
                ),
                ZoneDefinition(
                    label="User's parking spot",
                    polygon=[(0.6, 0.0), (1.0, 0.0), (1.0, 1.0)],
                ),
            ]
        )
    )


def test_run_uses_combined_analyser_and_passes_vid_analysis_to_notifier(
    tmp_path, monkeypatch
):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    stub_video_analyser = _StubVideoAnalyser()
    stub_notifier = _StubNotifierAgent()
    monkeypatch.setattr(pipeline_run, "analyse_video", stub_video_analyser)
    monkeypatch.setattr(pipeline_run, "notifier_agent", stub_notifier)

    asyncio.run(pipeline_run.run(video_path, _parking_config(), "video/mp4"))

    analyser_args, analyser_kwargs = stub_video_analyser.calls[0]
    assert analyser_args[0] == video_path
    assert analyser_args[1] == "video/mp4"
    assert [zone.label for zone in analyser_args[2]] == [
        "Neighbour's parking spot",
        "User's parking spot",
    ]
    assert analyser_kwargs["parking_spot_system_prompt"] == "custom parking prompt"
    notifier_call = stub_notifier.calls[0]
    assert '"parking_spot_status": "occupied"' in notifier_call["user_prompt"]
    assert '"vehicle_description": "a dark blue compact SUV"' in notifier_call["user_prompt"]
    assert '"events_description": "A car occupies' in notifier_call["user_prompt"]


def test_run_requires_parking_overlay(tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    with pytest.raises(ValueError, match="requires a parking spot overlay"):
        asyncio.run(pipeline_run.run(video_path, RunConfig(), "video/mp4"))


def test_build_svg_overlay_uses_thicker_zone_strokes():
    svg = _build_svg_overlay(
        [
            ZoneDefinition(
                label="Bay 1",
                color=Color.BLUE,
                polygon=[(0.1, 0.1), (0.9, 0.1), (0.9, 0.9)],
            )
        ],
        width=100,
        height=100,
    )

    assert 'stroke-width="4"' in svg


def test_run_persists_analysis_trace_ids_and_links_notification_to_analysis(
    tmp_path, monkeypatch
):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    stub_video_analyser = _StubVideoAnalyser()
    stub_notifier_agent = _StubNotifierAgent()
    monkeypatch.setattr(pipeline_run, "analyse_video", stub_video_analyser)
    monkeypatch.setattr(pipeline_run, "notifier_agent", stub_notifier_agent)

    class _FakeSpanContext:
        trace_id = int("019d78c3bdb09b4a7f86016d6b87d8e5", 16)
        span_id = int("5e4efff3ff52f591", 16)
        is_valid = True

    class _FakeSpan:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_span_context(self):
            return _FakeSpanContext()

    monkeypatch.setattr(
        pipeline_run.logfire, "span", lambda *_args, **_kwargs: _FakeSpan()
    )

    config = _parking_config()

    async def _run():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        await pipeline_run.run(
            video_path,
            config,
            "video/mp4",
            db=db,
            clip_start_time=datetime.fromisoformat("2026-04-10T09:00:00+00:00"),
            clip_end_time=datetime.fromisoformat("2026-04-10T09:00:30+00:00"),
        )
        analysis_records = await db.query_analyses(limit=10)
        return analysis_records, stub_notifier_agent.calls

    analysis_records, notifier_calls = asyncio.run(_run())

    assert len(analysis_records) == 1
    assert analysis_records[0].clip_start_time == datetime.fromisoformat(
        "2026-04-10T09:00:00+00:00"
    )
    assert analysis_records[0].clip_end_time == datetime.fromisoformat(
        "2026-04-10T09:00:30+00:00"
    )
    assert analysis_records[0].logfire_trace_id == "019d78c3bdb09b4a7f86016d6b87d8e5"
    assert analysis_records[0].logfire_span_id == "5e4efff3ff52f591"
    assert notifier_calls[0]["deps"].vid_analysis_id == analysis_records[0].id
    assert notifier_calls[0]["deps"].video_start_time == datetime.fromisoformat(
        "2026-04-10T09:00:00+00:00"
    )


def test_run_discards_video_while_vid_analyser_is_snoozed(
    tmp_path, monkeypatch, caplog
):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    stub_video_analyser = _StubVideoAnalyser()
    stub_notifier_agent = _StubNotifierAgent()
    monkeypatch.setattr(pipeline_run, "analyse_video", stub_video_analyser)
    monkeypatch.setattr(pipeline_run, "notifier_agent", stub_notifier_agent)
    caplog.set_level(logging.INFO, logger=pipeline_run.logger.name)

    config = RunConfig()

    async def _run():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        now = datetime.now(UTC)
        await db.insert_vid_analyser_snooze(
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(minutes=10),
            created_by="test",
            reason="quiet time",
        )
        result = await pipeline_run.run(video_path, config, "video/mp4", db=db)
        analysis_records = await db.query_analyses(limit=10)
        return result, analysis_records

    result, analysis_records = asyncio.run(_run())

    assert result is None
    assert analysis_records == []
    assert stub_video_analyser.calls == []
    assert stub_notifier_agent.calls == []
    assert "Discarding video analysis while snoozed" in caplog.text
