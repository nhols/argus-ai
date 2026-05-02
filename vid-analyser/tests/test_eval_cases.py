import sys
from pathlib import Path

import pytest

pydantic_evals = pytest.importorskip("pydantic_evals")
Case = pydantic_evals.Case
Dataset = pydantic_evals.Dataset

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.agent.vid_analyser import VidAnalysis  # noqa: E402
from vid_analyser.evals import (  # noqa: E402
    VideoCaseMetadata,
    VideoEvalDatasetManifest,
    VideoEvalDataset,
    load_video_case,
    load_video_dataset,
    load_video_dataset_manifest,
    make_video_case,
    save_video_case,
)


def test_make_video_case_uses_typed_input_output_and_metadata_models():
    expected_output = VidAnalysis(
        ir_mode=False,
        parking_spot_status="occupied",
        number_plate=None,
        events_description="A car is parked in the bay.",
    )
    case = make_video_case(
        name="occupied-daylight",
        filename="occupied-daylight.mp4",
        expected_output=expected_output,
        video_hash="abc123",
        tags=["daylight", "occupied"],
    )

    assert isinstance(case, Case)
    assert case.inputs.filename == "occupied-daylight.mp4"
    assert case.expected_output == expected_output
    assert case.metadata == VideoCaseMetadata(video_hash="abc123", tags=["daylight", "occupied"])


def test_video_eval_dataset_alias_accepts_typed_video_cases():
    case = make_video_case(name="unknown", filename="unknown.mp4", video_hash="abc123")

    dataset: VideoEvalDataset = Dataset(name="video-analysis", cases=[case])

    assert dataset.cases[0].inputs.filename == "unknown.mp4"
    assert dataset.cases[0].metadata == VideoCaseMetadata(video_hash="abc123")


def test_load_video_case_reads_migrated_case_data():
    repo_root = Path(__file__).resolve().parents[2]
    eval_data_dir = repo_root / "eval_data"

    case = load_video_case(
        eval_data_dir / "cases" / "20260204073042.json",
        videos_dir=eval_data_dir / "videos",
    )

    assert case.name == "20260204073042"
    assert case.inputs.filename == "20260204073042.mp4"
    assert case.expected_output == VidAnalysis(
        ir_mode=False,
        parking_spot_status="vacant",
        number_plate=None,
        events_description="- katie leaves the house",
    )
    assert case.metadata == VideoCaseMetadata(
        video_hash="1127055ae5d37851ec9403b9739d8db17779fa1f7c77f8fd7317126b2105829d",
        tags=[],
    )


def test_load_video_case_raises_for_missing_video_file(tmp_path):
    case_path = tmp_path / "cases" / "missing-video.json"
    save_video_case(
        case_path,
        make_video_case(
            name="missing-video",
            filename="missing-video.mp4",
            expected_output=VidAnalysis(
                ir_mode=False,
                parking_spot_status="unknown",
                number_plate=None,
                events_description="No video exists.",
            ),
            video_hash="abc123",
        ),
    )

    with pytest.raises(FileNotFoundError, match="missing-video.mp4"):
        load_video_case(case_path, videos_dir=tmp_path / "videos")


def test_load_video_dataset_reads_manifest_cases():
    repo_root = Path(__file__).resolve().parents[2]

    dataset = load_video_dataset(repo_root / "eval_data" / "datasets" / "parking-v1.json")

    assert dataset.name == "parking-v1"
    assert len(dataset.cases) == 25
    assert dataset.cases[0].inputs.filename == "20260204073042.mp4"


def test_load_video_dataset_manifest_formalises_dataset_layout():
    repo_root = Path(__file__).resolve().parents[2]

    layout = load_video_dataset_manifest(repo_root / "eval_data" / "datasets" / "parking-v1.json")

    assert layout.manifest == VideoEvalDatasetManifest(
        name="parking-v1",
        cases=[f"cases/{case.name}.json" for case in load_video_dataset(layout.manifest_path).cases],
    )
    assert layout.cases_dir == repo_root / "eval_data" / "cases"
    assert layout.videos_dir == repo_root / "eval_data" / "videos"


def test_save_video_case_overwrites_case_file(tmp_path):
    case_path = tmp_path / "cases" / "clip.json"
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    (videos_dir / "clip.mp4").write_bytes(b"video")
    first_case = make_video_case(
        name="clip",
        filename="clip.mp4",
        expected_output=VidAnalysis(
            ir_mode=False,
            parking_spot_status="unknown",
            number_plate=None,
            events_description="first",
        ),
        video_hash="abc123",
        tags=["first"],
    )
    second_case = make_video_case(
        name="clip",
        filename="clip.mp4",
        expected_output=VidAnalysis(
            ir_mode=True,
            parking_spot_status="occupied",
            number_plate="HX72LLM",
            events_description="second",
        ),
        video_hash="def456",
        tags=["second", "occupied"],
    )

    save_video_case(case_path, first_case)
    save_video_case(case_path, second_case)

    reloaded = load_video_case(case_path, videos_dir=videos_dir)
    assert reloaded.expected_output == second_case.expected_output
    assert reloaded.metadata == VideoCaseMetadata(video_hash="def456", tags=["second", "occupied"])
