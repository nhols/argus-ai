from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import logfire

SRC_DIR = Path(__file__).resolve().parents[3]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.config_schema import RunConfig  # noqa: E402
from vid_analyser.evals import load_video_dataset, load_video_dataset_manifest, video_analysis_evaluators  # noqa: E402
from vid_analyser.evals.cases import VideoCaseInput  # noqa: E402
from vid_analyser.pipeline.run import analyse_video  # noqa: E402


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "eval_data").is_dir():
            return parent
    raise FileNotFoundError("Could not find repo root containing eval_data")


def _load_config(path: Path | None) -> RunConfig:
    if path is None:
        return RunConfig()
    return RunConfig.model_validate(json.loads(path.read_text()))


def main() -> None:
    repo_root = _repo_root()
    parser = argparse.ArgumentParser(description="Run the current video analyser agent against an eval dataset.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "eval_data" / "datasets" / "parking-v1.json",
        help="Path to an eval dataset manifest.",
    )
    parser.add_argument(
        "--config-json",
        type=Path,
        default=None,
        help="Optional JSON file containing a RunConfig document.",
    )
    parser.add_argument("--max-concurrency", type=int, default=10)
    args = parser.parse_args()

    logfire.configure(send_to_logfire="if-token-present", service_name="vid-analyser-evals")

    config = _load_config(args.config_json)
    layout = load_video_dataset_manifest(args.manifest)
    dataset = load_video_dataset(args.manifest)
    dataset.evaluators = list(video_analysis_evaluators())

    async def task(inputs: VideoCaseInput):
        return await analyse_video(
            layout.videos_dir / inputs.filename,
            config,
            "video/mp4",
        )

    report = dataset.evaluate_sync(
        task,
        name="current-vid-analyser",
        task_name="vid_analyser_agent",
        max_concurrency=args.max_concurrency,
        metadata={
            "manifest": str(args.manifest),
            "videos_dir": str(layout.videos_dir),
            "config_json": str(args.config_json) if args.config_json is not None else None,
        },
    )
    report.print(include_input=True, include_output=True, include_expected_output=True)


if __name__ == "__main__":
    main()
