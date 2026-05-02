import json
from pathlib import Path
from typing import TypeAlias

from pydantic import BaseModel, Field
from pydantic_evals import Case, Dataset
from vid_analyser.agent.vid_analyser import VidAnalysis


class VideoCaseInput(BaseModel):
    filename: str = Field(description="Video filename to analyse.")


class VideoCaseMetadata(BaseModel):
    video_hash: str = Field(description="SHA-256 hash of the source video file.")
    tags: list[str] = Field(default_factory=list, description="User-defined labels for grouping cases.")


VideoEvalCase: TypeAlias = Case[VideoCaseInput, VidAnalysis, VideoCaseMetadata]
VideoEvalDataset: TypeAlias = Dataset[VideoCaseInput, VidAnalysis, VideoCaseMetadata]


def make_video_case(
    *,
    name: str,
    filename: str,
    expected_output: VidAnalysis | None = None,
    video_hash: str,
    tags: list[str] | None = None,
) -> VideoEvalCase:
    return Case(
        name=name,
        inputs=VideoCaseInput(filename=filename),
        expected_output=expected_output,
        metadata=VideoCaseMetadata(video_hash=video_hash, tags=tags or []),
    )


def video_case_to_dict(case: VideoEvalCase) -> dict:
    return {
        "name": case.name,
        "inputs": case.inputs.model_dump(mode="json"),
        "expected_output": case.expected_output.model_dump(mode="json") if case.expected_output is not None else None,
        "metadata": case.metadata.model_dump(mode="json") if case.metadata is not None else None,
    }


def save_video_case(path: Path, case: VideoEvalCase) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(video_case_to_dict(case), indent=2, sort_keys=False) + "\n")


def load_video_case(path: Path, *, videos_dir: Path) -> VideoEvalCase:
    data = json.loads(path.read_text())
    inputs = VideoCaseInput.model_validate(data["inputs"])
    video_path = videos_dir / inputs.filename
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    return Case(
        name=data["name"],
        inputs=inputs,
        expected_output=VidAnalysis.model_validate(data["expected_output"]),
        metadata=VideoCaseMetadata.model_validate(data["metadata"]),
    )
