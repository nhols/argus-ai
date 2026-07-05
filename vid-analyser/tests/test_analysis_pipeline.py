import asyncio
import importlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai import BinaryContent

from vid_analyser.agent.models import ParkingSpotAssessment, SceneAnalysis, VidAnalysis
from vid_analyser.agent.analysis.scene import get_parking_assessment
from vid_analyser.overlay_schema import ZoneDefinition

analysis_run = importlib.import_module("vid_analyser.agent.analysis.run")
scene_module = importlib.import_module("vid_analyser.agent.analysis.scene")


def test_analysis_runs_parking_then_scene_and_merges_results(monkeypatch):
    calls = []
    overlay_paths = []
    parking = ParkingSpotAssessment(
        parking_spot_status="occupied",
        number_plate="HX72 LLM",
        vehicle_description="a dark blue compact SUV",
    )

    async def assess_parking_spot(video_path, parking_zone):
        calls.append("parking")
        return parking

    def generate_overlay_reference_frame(video_path, zones, *, output_dir):
        overlay_path = output_dir / "clip_zones.png"
        overlay_path.write_bytes(b"overlay")
        overlay_paths.append(overlay_path)
        return overlay_path

    async def analyse_scene(
        video_path,
        content_type,
        overlay_reference_path,
        overlay_zones,
        parking_assessment,
        **kwargs,
    ):
        calls.append("scene")
        assert parking_assessment is parking
        assert overlay_reference_path.read_bytes() == b"overlay"
        return SceneAnalysis(
            ir_mode=False,
            events_description="A car occupies the user's parking spot.",
        )

    monkeypatch.setattr(analysis_run, "assess_parking_spot", assess_parking_spot)
    monkeypatch.setattr(analysis_run, "analyse_scene", analyse_scene)
    monkeypatch.setattr(
        analysis_run,
        "generate_overlay_reference_frame",
        generate_overlay_reference_frame,
    )

    zones = [
        ZoneDefinition(label="User's parking spot", polygon=[(0, 0), (1, 0), (1, 1)])
    ]

    result = asyncio.run(
        analysis_run.analyse_video(
            Path("clip.mp4"),
            "video/mp4",
            zones,
            scene_system_prompt=None,
            video_start_time=datetime.now(UTC),
        )
    )

    assert calls == ["parking", "scene"]
    assert overlay_paths and not overlay_paths[0].parent.exists()
    assert result == VidAnalysis(
        ir_mode=False,
        parking_spot_status="occupied",
        number_plate="HX72 LLM",
        vehicle_description="a dark blue compact SUV",
        events_description="A car occupies the user's parking spot.",
    )


def test_scene_agent_receives_authoritative_parking_assessment():
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            parking_assessment=ParkingSpotAssessment(
                parking_spot_status="car leaving",
                number_plate="HX72 LLM",
                vehicle_description="a large dark SUV",
            )
        )
    )

    instructions = get_parking_assessment(ctx)

    assert "Authoritative parking-spot assessment" in instructions
    assert '"parking_spot_status": "car leaving"' in instructions
    assert '"number_plate": "HX72 LLM"' in instructions
    assert '"vehicle_description": "a large dark SUV"' in instructions


def test_scene_agent_receives_video_and_overlay(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.mp4"
    overlay_path = tmp_path / "clip_zones.png"
    video_path.write_bytes(b"video")
    overlay_path.write_bytes(b"overlay")

    class StubAgent:
        async def run(self, prompt, **kwargs):
            self.prompt = prompt
            return SimpleNamespace(
                output=SceneAnalysis(ir_mode=False, events_description="description")
            )

    agent = StubAgent()
    monkeypatch.setattr(scene_module, "scene_agent", agent)

    asyncio.run(
        scene_module.analyse_scene(
            video_path,
            "video/mp4",
            overlay_path,
            [ZoneDefinition(label="Doorstep", polygon=[(0, 0), (1, 0), (1, 1)])],
            ParkingSpotAssessment(parking_spot_status="vacant", number_plate=None),
            system_prompt=None,
            video_start_time=datetime.now(UTC),
        )
    )

    binary_inputs = [item for item in agent.prompt if isinstance(item, BinaryContent)]
    assert [item.data for item in binary_inputs] == [b"video", b"overlay"]
    assert binary_inputs[1].identifier == "static_image"
    assert "Doorstep" in agent.prompt[2]


def test_analysis_overlay_is_cleaned_up_when_scene_fails(monkeypatch):
    overlay_paths = []

    def generate_overlay_reference_frame(video_path, zones, *, output_dir):
        overlay_path = output_dir / "clip_zones.png"
        overlay_path.write_bytes(b"overlay")
        overlay_paths.append(overlay_path)
        return overlay_path

    async def assess_parking_spot(video_path, parking_zone):
        return ParkingSpotAssessment(parking_spot_status="vacant", number_plate=None)

    async def analyse_scene(*args, **kwargs):
        raise RuntimeError("scene failed")

    monkeypatch.setattr(
        analysis_run,
        "generate_overlay_reference_frame",
        generate_overlay_reference_frame,
    )
    monkeypatch.setattr(analysis_run, "assess_parking_spot", assess_parking_spot)
    monkeypatch.setattr(analysis_run, "analyse_scene", analyse_scene)

    with pytest.raises(RuntimeError, match="scene failed"):
        asyncio.run(
            analysis_run.analyse_video(
                Path("clip.mp4"),
                "video/mp4",
                [
                    ZoneDefinition(
                        label="User's parking spot", polygon=[(0, 0), (1, 0), (1, 1)]
                    )
                ],
                scene_system_prompt=None,
                video_start_time=datetime.now(UTC),
            )
        )

    assert overlay_paths and not overlay_paths[0].parent.exists()
