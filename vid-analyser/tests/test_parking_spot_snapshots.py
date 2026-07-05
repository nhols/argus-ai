import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

pydantic_evals = pytest.importorskip("pydantic_evals")

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.agent.vid_analyser import VidAnalysis  # noqa: E402
from vid_analyser.evals.cases import make_video_case  # noqa: E402
from vid_analyser.evals.parking_spot_snapshots import (  # noqa: E402
    ParkingSpotAssessment,
    assess_parking_spot,
    find_zone,
    make_parking_spot_dataset,
    snapshot_timestamps,
    zone_bounding_box,
)
from vid_analyser.agent.analysis.parking_spot import PROMPT  # noqa: E402
from vid_analyser.overlay_schema import ZoneDefinition  # noqa: E402
from pydantic_ai import BinaryContent  # noqa: E402


def test_parking_prompt_forbids_colour_claims_in_ir_mode():
    assert "If the images use IR/night vision, do not comment" in PROMPT


def test_snapshot_timestamps_cover_video_in_order_without_exact_boundaries():
    timestamps = snapshot_timestamps(10.0, 5)

    assert timestamps == pytest.approx([0.1, 2.55, 5.0, 7.45, 9.9])


def test_zone_bounding_box_scales_normalized_polygon_and_adds_padding():
    zone = ZoneDefinition(
        label="User's parking spot",
        polygon=[(0.5, 0.25), (0.75, 0.25), (0.75, 0.5), (0.5, 0.5)],
    )

    assert zone_bounding_box(zone, width=1000, height=500) == (494, 119, 262, 137)


def test_find_zone_is_case_insensitive_and_rejects_ambiguous_config():
    zone = ZoneDefinition(label="User's Parking Spot", polygon=[(0, 0), (1, 0), (1, 1)])

    assert find_zone([zone], "user's parking spot") is zone
    with pytest.raises(ValueError, match="found 0"):
        find_zone([], "user's parking spot")


def test_make_parking_spot_dataset_discards_unrelated_analysis_fields():
    source = pydantic_evals.Dataset(
        name="parking-v1",
        cases=[
            make_video_case(
                name="occupied",
                filename="occupied.mp4",
                video_hash="abc",
                expected_output=VidAnalysis(
                    ir_mode=False,
                    parking_spot_status="occupied",
                    number_plate="HX72 LLM",
                    vehicle_description="a dark blue compact SUV",
                    events_description="Unrelated prose is intentionally discarded.",
                ),
            )
        ],
    )

    dataset = make_parking_spot_dataset(source)

    assert dataset.name == "parking-v1-parking-spot-snapshots"
    assert dataset.cases[0].expected_output == ParkingSpotAssessment(
        parking_spot_status="occupied",
        number_plate="HX72 LLM",
        vehicle_description="a dark blue compact SUV",
    )


def test_assess_parking_spot_sends_ordered_identified_snapshots(tmp_path, monkeypatch):
    import asyncio

    import vid_analyser.agent.analysis.parking_spot as snapshots_module

    paths = [tmp_path / "one.jpg", tmp_path / "two.jpg"]
    paths[0].write_bytes(b"one")
    paths[1].write_bytes(b"two")

    @contextmanager
    def fake_boxed_snapshots(*_args, **_kwargs):
        yield paths

    class StubAgent:
        def __init__(self):
            self.prompt = None

        async def run(self, prompt):
            self.prompt = prompt
            return SimpleNamespace(
                output=ParkingSpotAssessment(
                    parking_spot_status="vacant", number_plate=None
                )
            )

    agent = StubAgent()
    monkeypatch.setattr(snapshots_module, "boxed_snapshots", fake_boxed_snapshots)
    monkeypatch.setattr(snapshots_module, "snapshot_agent", lambda _model: agent)
    zone = ZoneDefinition(label="spot", polygon=[(0, 0), (1, 0), (1, 1)])

    result = asyncio.run(assess_parking_spot(tmp_path / "clip.mp4", zone, count=2))

    assert result.parking_spot_status == "vacant"
    images = [part for part in agent.prompt if isinstance(part, BinaryContent)]
    assert [image.identifier for image in images] == ["snapshot_01", "snapshot_02"]
    assert [image.data for image in images] == [b"one", b"two"]
    assert all(image.media_type == "image/jpeg" for image in images)


def test_boxed_snapshots_are_cleaned_up_after_failure(tmp_path, monkeypatch):
    import vid_analyser.agent.analysis.parking_spot as snapshots_module

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        snapshots_module,
        "probe_video",
        lambda _path: SimpleNamespace(width=100, height=100, duration=10.0),
    )

    def fake_extract(_video, output, _timestamp, _box):
        output.write_bytes(b"frame")

    monkeypatch.setattr(snapshots_module, "_extract_snapshot", fake_extract)
    zone = ZoneDefinition(label="spot", polygon=[(0, 0), (1, 0), (1, 1)])
    snapshot_paths = []

    with pytest.raises(RuntimeError, match="scene failed"):
        with snapshots_module.boxed_snapshots(video_path, zone, count=2) as paths:
            snapshot_paths.extend(paths)
            raise RuntimeError("scene failed")

    assert snapshot_paths
    assert all(not path.exists() for path in snapshot_paths)
    assert not snapshot_paths[0].parent.exists()
