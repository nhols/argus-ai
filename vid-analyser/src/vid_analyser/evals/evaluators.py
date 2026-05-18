from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import models

from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.evaluators.llm_as_a_judge import judge_output_expected
from rapidfuzz.distance import Levenshtein
from vid_analyser.agent.models import DEFAULT_GOOGLE_MODEL
from vid_analyser.agent.retry import create_google_retry_model
from vid_analyser.agent.vid_analyser import VidAnalysis
from vid_analyser.evals.cases import VideoCaseInput, VideoCaseMetadata

DEFAULT_EVENTS_DESCRIPTION_RUBRIC = """
Score how well the actual events description matches the expected events description.

Return a score from 0 to 1:
- 1.0 means the actual description captures all important expected visible events with no material contradictions.
- 0.5 means it captures some important expected events but misses or weakens others.
- 0.0 means it is unrelated, mostly incorrect, or contradicts the expected events.

Allow differences in wording, ordering, and level of prose polish. Penalize missing important actions, invented actions,
incorrect people, incorrect vehicle movement, or contradictions about what happened.
"""
DEFAULT_EVENTS_DESCRIPTION_JUDGE_MODEL = DEFAULT_GOOGLE_MODEL


def _missing_expected_output(field_name: str) -> EvaluationReason:
    return EvaluationReason(value=0.0, reason=f"Missing expected output for {field_name}.")


def _exact_score(actual: Any, expected: Any, field_name: str) -> EvaluationReason:
    if actual == expected:
        return EvaluationReason(value=1.0, reason=f"{field_name} matched exactly.")
    return EvaluationReason(value=0.0, reason=f"{field_name} mismatch: expected {expected!r}, got {actual!r}.")


def normalize_number_plate(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def number_plate_similarity(actual: str | None, expected: str | None) -> float:
    if actual is None and expected is None:
        return 1.0
    if actual is None or expected is None:
        return 0.0

    normalized_actual = normalize_number_plate(actual)
    normalized_expected = normalize_number_plate(expected)
    distance = Levenshtein.normalized_distance(normalized_actual, normalized_expected)
    return 1.0 - distance


@dataclass
class IrModeEvaluator(Evaluator[VideoCaseInput, VidAnalysis, VideoCaseMetadata]):
    evaluation_name: str = "ir_mode"

    def evaluate(self, ctx: EvaluatorContext[VideoCaseInput, VidAnalysis, VideoCaseMetadata]) -> EvaluationReason:
        if ctx.expected_output is None:
            return _missing_expected_output(self.evaluation_name)
        return _exact_score(ctx.output.ir_mode, ctx.expected_output.ir_mode, self.evaluation_name)


@dataclass
class ParkingSpotStatusEvaluator(Evaluator[VideoCaseInput, VidAnalysis, VideoCaseMetadata]):
    evaluation_name: str = "parking_spot_status"

    def evaluate(self, ctx: EvaluatorContext[VideoCaseInput, VidAnalysis, VideoCaseMetadata]) -> EvaluationReason:
        if ctx.expected_output is None:
            return _missing_expected_output(self.evaluation_name)
        return _exact_score(
            ctx.output.parking_spot_status,
            ctx.expected_output.parking_spot_status,
            self.evaluation_name,
        )


@dataclass
class NumberPlateEvaluator(Evaluator[VideoCaseInput, VidAnalysis, VideoCaseMetadata]):
    evaluation_name: str = "number_plate"

    def evaluate(self, ctx: EvaluatorContext[VideoCaseInput, VidAnalysis, VideoCaseMetadata]) -> EvaluationReason:
        if ctx.expected_output is None:
            return _missing_expected_output(self.evaluation_name)

        score = number_plate_similarity(ctx.output.number_plate, ctx.expected_output.number_plate)
        return EvaluationReason(
            value=score,
            reason=f"Normalized number plates: expected {ctx.expected_output.number_plate!r}, got {ctx.output.number_plate!r}.",
        )


@dataclass
class EventsDescriptionEvaluator(Evaluator[VideoCaseInput, VidAnalysis, VideoCaseMetadata]):
    evaluation_name: str = "events_description"
    rubric: str = DEFAULT_EVENTS_DESCRIPTION_RUBRIC
    model: models.Model | models.KnownModelName | str | None = field(
        default_factory=lambda: create_google_retry_model(DEFAULT_EVENTS_DESCRIPTION_JUDGE_MODEL)
    )

    async def evaluate(self, ctx: EvaluatorContext[VideoCaseInput, VidAnalysis, VideoCaseMetadata]) -> EvaluationReason:
        if ctx.expected_output is None:
            return _missing_expected_output(self.evaluation_name)

        grading = await judge_output_expected(
            output=ctx.output.events_description,
            expected_output=ctx.expected_output.events_description,
            rubric=self.rubric,
            model=self.model,
        )
        return EvaluationReason(value=max(0.0, min(1.0, grading.score)), reason=grading.reason)


def video_analysis_evaluators() -> tuple[Evaluator[VideoCaseInput, VidAnalysis, VideoCaseMetadata], ...]:
    return (
        IrModeEvaluator(),
        ParkingSpotStatusEvaluator(),
        NumberPlateEvaluator(),
        EventsDescriptionEvaluator(),
    )
