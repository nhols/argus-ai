import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai import BinaryContent
from vid_analyser.db import init_database

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.config_schema import OverlayConfig, RunConfig
from vid_analyser.overlay import _build_svg_overlay
from vid_analyser.overlay_schema import Color, ZoneDefinition
from vid_analyser.pipeline import run as pipeline_run


class _StubVidAnalyserAgent:
    def __init__(self):
        self.calls = []

    async def run(self, analysis_inputs, **kwargs):
        self.calls.append((analysis_inputs, kwargs))
        return SimpleNamespace(
            output=SimpleNamespace(
                model_dump_json=lambda **_kwargs: '{"events_description":"ok"}',
            )
        )


class _StubNotifierAgent:
    def __init__(self):
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output="sent")


def test_run_attaches_static_image_identifier_in_user_message(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    overlay_path = tmp_path / "clip_zones.png"
    overlay_path.write_bytes(b"png")

    stub_vid_agent = _StubVidAnalyserAgent()
    monkeypatch.setattr(pipeline_run, "vid_analyser_agent", stub_vid_agent)
    monkeypatch.setattr(pipeline_run, "notifier_agent", _StubNotifierAgent())
    monkeypatch.setattr(
        pipeline_run,
        "generate_overlay_reference_frame",
        lambda _video, _zones: overlay_path,
    )

    config = RunConfig(
        overlay=OverlayConfig(
            zones=[
                ZoneDefinition(
                    label="Bay 1",
                    color=Color.RED,
                    polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
                )
            ]
        )
    )

    asyncio.run(pipeline_run.run(video_path, config, "video/mp4"))

    analysis_inputs, kwargs = stub_vid_agent.calls[0]
    assert analysis_inputs[2] == (
        "File static_image is a static reference image taken from this video. "
        "The overlay zones below relate to static_image. "
        "Pay close attention to those zones when analysing static_image and the video.\n"
        "The overlay zones for file static_image are:\nBay 1 (color: RED)"
    )
    assert analysis_inputs[3] == "This is file static_image from the video:"
    assert isinstance(analysis_inputs[4], BinaryContent)
    assert analysis_inputs[4].identifier == "static_image"
    assert analysis_inputs[4].media_type == "image/png"
    assert not hasattr(kwargs["deps"], "overlay_zones_descriptions")


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


def test_run_persists_analysis_trace_ids_and_links_notification_to_analysis(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    stub_vid_agent = _StubVidAnalyserAgent()
    stub_notifier_agent = _StubNotifierAgent()
    monkeypatch.setattr(pipeline_run, "vid_analyser_agent", stub_vid_agent)
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

    monkeypatch.setattr(pipeline_run.logfire, "span", lambda *_args, **_kwargs: _FakeSpan())

    config = RunConfig()

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
    assert analysis_records[0].clip_start_time == datetime.fromisoformat("2026-04-10T09:00:00+00:00")
    assert analysis_records[0].clip_end_time == datetime.fromisoformat("2026-04-10T09:00:30+00:00")
    assert analysis_records[0].logfire_trace_id == "019d78c3bdb09b4a7f86016d6b87d8e5"
    assert analysis_records[0].logfire_span_id == "5e4efff3ff52f591"
    assert notifier_calls[0]["deps"].vid_analysis_id == analysis_records[0].id
