import sys
from pathlib import Path

import pytest

pydantic_evals = pytest.importorskip("pydantic_evals")
EvaluationReason = pydantic_evals.evaluators.EvaluationReason
EvaluatorContext = pydantic_evals.evaluators.EvaluatorContext

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.agent.vid_analyser import VidAnalysis  # noqa: E402
from vid_analyser.evals import (  # noqa: E402
    EventsDescriptionEvaluator,
    IrModeEvaluator,
    NumberPlateEvaluator,
    ParkingSpotStatusEvaluator,
    VideoCaseInput,
    VideoCaseMetadata,
    normalize_number_plate,
    number_plate_similarity,
    video_analysis_evaluators,
)


def _analysis(**overrides):
    data = {
        "ir_mode": False,
        "parking_spot_status": "occupied",
        "number_plate": "HX72 LLM",
        "events_description": "A car is parked in the bay.",
    }
    data.update(overrides)
    return VidAnalysis(**data)


def _ctx(output: VidAnalysis, expected_output: VidAnalysis | None = None):
    return EvaluatorContext(
        name="case",
        inputs=VideoCaseInput(filename="clip.mp4"),
        metadata=VideoCaseMetadata(video_hash="abc123"),
        expected_output=expected_output,
        output=output,
        duration=0.0,
        _span_tree=None,
        attributes={},
        metrics={},
    )


def test_ir_mode_evaluator_scores_exact_match():
    result = IrModeEvaluator().evaluate(_ctx(_analysis(ir_mode=True), _analysis(ir_mode=True)))

    assert result == EvaluationReason(value=1.0, reason="ir_mode matched exactly.")


def test_parking_spot_status_evaluator_scores_mismatch():
    result = ParkingSpotStatusEvaluator().evaluate(
        _ctx(
            _analysis(parking_spot_status="vacant"),
            _analysis(parking_spot_status="occupied"),
        )
    )

    assert result.value == 0.0
    assert "parking_spot_status mismatch" in result.reason


def test_number_plate_evaluator_normalizes_case_and_whitespace():
    result = NumberPlateEvaluator().evaluate(
        _ctx(
            _analysis(number_plate="hx 72 llm"),
            _analysis(number_plate="HX72LLM"),
        )
    )

    assert result.value == 1.0


def test_number_plate_similarity_uses_levenshtein_distance():
    assert normalize_number_plate(" hx 72 llm ") == "HX72LLM"
    assert number_plate_similarity("HX72LLM", "HX72LLN") == pytest.approx(6 / 7)
    assert number_plate_similarity(None, None) == 1.0
    assert number_plate_similarity(None, "HX72LLM") == 0.0


def test_video_analysis_evaluators_contains_all_criteria():
    evaluators = video_analysis_evaluators()

    assert [evaluator.evaluation_name for evaluator in evaluators] == [
        "ir_mode",
        "parking_spot_status",
        "number_plate",
        "events_description",
    ]
    assert isinstance(evaluators[-1], EventsDescriptionEvaluator)
