import json
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_evals import Dataset
from vid_analyser.evals.cases import (
    VideoCaseInput,
    VideoCaseMetadata,
    VideoEvalDataset,
    load_video_case,
)
from vid_analyser.agent.vid_analyser import VidAnalysis


class VideoEvalDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    cases_dir: str = Field(default="cases")
    videos_dir: str = Field(default="videos")
    cases: list[str]

    @field_validator("cases_dir", "videos_dir")
    @classmethod
    def _validate_relative_dir(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("must be a relative path inside eval_data")
        return value

    @field_validator("cases")
    @classmethod
    def _validate_case_paths(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("case paths must be relative paths inside eval_data")
            if value in seen:
                raise ValueError(f"duplicate case path: {value}")
            seen.add(value)
        return values


class VideoEvalDatasetLayout(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    eval_data_dir: Path
    manifest_path: Path
    manifest: VideoEvalDatasetManifest

    @property
    def cases_dir(self) -> Path:
        return self.eval_data_dir / self.manifest.cases_dir

    @property
    def videos_dir(self) -> Path:
        return self.eval_data_dir / self.manifest.videos_dir

    def case_paths(self) -> list[Path]:
        return [self.eval_data_dir / case_path for case_path in self.manifest.cases]


def load_video_dataset_manifest(manifest_path: Path) -> VideoEvalDatasetLayout:
    manifest = VideoEvalDatasetManifest.model_validate(json.loads(manifest_path.read_text()))
    eval_data_dir = manifest_path.parents[1]

    if not (eval_data_dir / manifest.cases_dir).is_dir():
        raise FileNotFoundError(eval_data_dir / manifest.cases_dir)
    if not (eval_data_dir / manifest.videos_dir).is_dir():
        raise FileNotFoundError(eval_data_dir / manifest.videos_dir)

    layout = VideoEvalDatasetLayout(
        eval_data_dir=eval_data_dir,
        manifest_path=manifest_path,
        manifest=manifest,
    )

    missing_case_paths = [case_path for case_path in layout.case_paths() if not case_path.exists()]
    if missing_case_paths:
        raise FileNotFoundError(missing_case_paths[0])

    return layout


def load_video_dataset(manifest_path: Path) -> VideoEvalDataset:
    layout = load_video_dataset_manifest(manifest_path)
    cases = [load_video_case(case_path, videos_dir=layout.videos_dir) for case_path in layout.case_paths()]
    return Dataset[VideoCaseInput, VidAnalysis, VideoCaseMetadata](name=layout.manifest.name, cases=cases)
