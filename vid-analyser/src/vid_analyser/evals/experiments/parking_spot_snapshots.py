from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import logfire

SRC_DIR = Path(__file__).resolve().parents[3]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.agent.models import DEFAULT_GOOGLE_MODEL  # noqa: E402
from vid_analyser.config_schema import RunConfig  # noqa: E402
from vid_analyser.evals import load_video_dataset, load_video_dataset_manifest  # noqa: E402
from vid_analyser.evals.cases import VideoCaseInput  # noqa: E402
from vid_analyser.evals.parking_spot_snapshots import (  # noqa: E402
    DEFAULT_SNAPSHOT_COUNT,
    DEFAULT_ZONE_LABEL,
    assess_parking_spot,
    find_zone,
    make_parking_spot_dataset,
    parking_spot_evaluators,
)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "eval_data").is_dir():
            return parent
    raise FileNotFoundError("Could not find repo root containing eval_data")


def main() -> None:
    repo_root = _repo_root()
    parser = argparse.ArgumentParser(
        description="Evaluate a parking-spot specialist using boxed, time-ordered video snapshots."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "eval_data" / "datasets" / "parking-v1.json",
    )
    parser.add_argument(
        "--config-json",
        type=Path,
        default=repo_root / "vid-analyser" / "config" / "config.json",
        help="RunConfig JSON containing the target overlay zone.",
    )
    parser.add_argument("--zone-label", default=DEFAULT_ZONE_LABEL)
    parser.add_argument("--snapshot-count", type=int, default=DEFAULT_SNAPSHOT_COUNT)
    parser.add_argument("--model", default=DEFAULT_GOOGLE_MODEL)
    parser.add_argument("--max-concurrency", type=int, default=5)
    args = parser.parse_args()

    logfire.configure(send_to_logfire="if-token-present", service_name="vid-analyser-evals")

    config = RunConfig.model_validate(json.loads(args.config_json.read_text()))
    if config.overlay is None:
        raise ValueError(f"No overlay is configured in {args.config_json}")
    zone = find_zone(config.overlay.zones, args.zone_label)
    layout = load_video_dataset_manifest(args.manifest)
    dataset = make_parking_spot_dataset(load_video_dataset(args.manifest))
    dataset.evaluators = list(parking_spot_evaluators())

    async def task(inputs: VideoCaseInput):
        return await assess_parking_spot(
            layout.videos_dir / inputs.filename,
            zone,
            count=args.snapshot_count,
            model_name=args.model,
        )

    report = dataset.evaluate_sync(
        task,
        name=f"parking-spot-snapshots-{args.snapshot_count}",
        task_name="parking_spot_snapshot_specialist",
        max_concurrency=args.max_concurrency,
        metadata={
            "manifest": str(args.manifest),
            "config_json": str(args.config_json),
            "zone_label": args.zone_label,
            "snapshot_count": args.snapshot_count,
            "model": args.model,
        },
    )
    report.print(include_input=True, include_output=True, include_expected_output=True)


if __name__ == "__main__":
    main()
