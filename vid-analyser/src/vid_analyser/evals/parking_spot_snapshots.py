from dataclasses import dataclass

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from vid_analyser.agent.models import ParkingSpotAssessment
from vid_analyser.agent.analysis.parking_spot import (
    DEFAULT_SNAPSHOT_COUNT,
    DEFAULT_ZONE_LABEL,
    assess_parking_spot,
    boxed_snapshots,
    find_zone,
    snapshot_agent,
    snapshot_timestamps,
    zone_bounding_box,
)
from vid_analyser.evals.cases import VideoCaseInput, VideoCaseMetadata, VideoEvalDataset
from vid_analyser.evals.evaluators import number_plate_similarity

ParkingSpotDataset = Dataset[VideoCaseInput, ParkingSpotAssessment, VideoCaseMetadata]


def make_parking_spot_dataset(source: VideoEvalDataset) -> ParkingSpotDataset:
    cases = [
        Case(
            name=case.name,
            inputs=case.inputs,
            expected_output=(
                ParkingSpotAssessment(
                    parking_spot_status=case.expected_output.parking_spot_status,
                    number_plate=case.expected_output.number_plate,
                    vehicle_description=case.expected_output.vehicle_description,
                )
                if case.expected_output
                else None
            ),
            metadata=case.metadata,
        )
        for case in source.cases
    ]
    return Dataset(name=f"{source.name}-parking-spot-snapshots", cases=cases)


@dataclass
class SnapshotParkingStatusEvaluator(
    Evaluator[VideoCaseInput, ParkingSpotAssessment, VideoCaseMetadata]
):
    evaluation_name: str = "parking_spot_status"

    def evaluate(
        self,
        ctx: EvaluatorContext[VideoCaseInput, ParkingSpotAssessment, VideoCaseMetadata],
    ) -> EvaluationReason:
        if ctx.expected_output is None:
            return EvaluationReason(
                value=0.0, reason="Missing expected parking spot assessment."
            )
        actual = ctx.output.parking_spot_status
        expected = ctx.expected_output.parking_spot_status
        return EvaluationReason(
            value=float(actual == expected),
            reason="parking_spot_status matched exactly."
            if actual == expected
            else f"Expected {expected!r}, got {actual!r}.",
        )


@dataclass
class SnapshotNumberPlateEvaluator(
    Evaluator[VideoCaseInput, ParkingSpotAssessment, VideoCaseMetadata]
):
    evaluation_name: str = "number_plate"

    def evaluate(
        self,
        ctx: EvaluatorContext[VideoCaseInput, ParkingSpotAssessment, VideoCaseMetadata],
    ) -> EvaluationReason:
        if ctx.expected_output is None:
            return EvaluationReason(
                value=0.0, reason="Missing expected parking spot assessment."
            )
        actual = ctx.output.number_plate
        expected = ctx.expected_output.number_plate
        return EvaluationReason(
            value=number_plate_similarity(actual, expected),
            reason=f"Normalized number plates: expected {expected!r}, got {actual!r}.",
        )


def parking_spot_evaluators() -> tuple[
    Evaluator[VideoCaseInput, ParkingSpotAssessment, VideoCaseMetadata], ...
]:
    return SnapshotParkingStatusEvaluator(), SnapshotNumberPlateEvaluator()


__all__ = [
    "DEFAULT_SNAPSHOT_COUNT",
    "DEFAULT_ZONE_LABEL",
    "ParkingSpotAssessment",
    "assess_parking_spot",
    "boxed_snapshots",
    "find_zone",
    "make_parking_spot_dataset",
    "parking_spot_evaluators",
    "snapshot_agent",
    "snapshot_timestamps",
    "zone_bounding_box",
]
