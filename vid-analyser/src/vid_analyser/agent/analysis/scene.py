from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from google.genai.types import MediaResolution
from pydantic_ai import Agent, BinaryContent, RunContext
from pydantic_ai.models.google import GoogleModelSettings
from vid_analyser.agent.models import (
    DEFAULT_GOOGLE_MODEL,
    ParkingSpotAssessment,
    SceneAnalysis,
)
from vid_analyser.agent.retry import create_google_retry_model
from vid_analyser.agent.utils import get_timestamps
from vid_analyser.overlay import zone_descriptions
from vid_analyser.overlay_schema import ZoneDefinition

DEFAULT_PROMPT = """
You analyse short security-camera videos.

Describe the important visible activity, including people, animals, vehicles, weather, and activity near the
property. Report only what is visible and prefer conservative wording when visibility is poor.

An authoritative parking-spot assessment is supplied separately. Include it accurately in the event description
without independently reassessing the parking spot or number plate.
"""


@dataclass
class Deps:
    parking_assessment: ParkingSpotAssessment
    system_prompt: str | None
    video_start_time: datetime


scene_agent = Agent[Deps, SceneAnalysis](
    model=create_google_retry_model(DEFAULT_GOOGLE_MODEL),
    output_type=SceneAnalysis,
    deps_type=Deps,
)


@scene_agent.instructions
def get_instructions(ctx: RunContext[Deps]) -> str:
    return ctx.deps.system_prompt or DEFAULT_PROMPT


@scene_agent.instructions
def get_parking_assessment(ctx: RunContext[Deps]) -> str:
    return (
        "Authoritative parking-spot assessment:\n"
        f"{ctx.deps.parking_assessment.model_dump_json(indent=2)}\n"
        "Use these parking facts in the event description without contradicting them."
    )


@scene_agent.instructions
def set_timestamps(ctx: RunContext[Deps]) -> str:
    return get_timestamps(ctx.deps.video_start_time)


async def analyse_scene(
    video_path: Path,
    content_type: str,
    overlay_reference_path: Path,
    overlay_zones: list[ZoneDefinition],
    parking_assessment: ParkingSpotAssessment,
    *,
    system_prompt: str | None,
    video_start_time: datetime,
) -> SceneAnalysis:
    result = await scene_agent.run(
        [
            "Analyse this video using the authoritative parking assessment.",
            BinaryContent(
                video_path.read_bytes(),
                media_type=content_type,
                vendor_metadata={"fps": 5.0},
            ),
            (
                "File static_image is a reference frame from the video. Its labelled zones are:\n"
                f"{zone_descriptions(overlay_zones)}"
            ),
            "This is file static_image:",
            BinaryContent(
                overlay_reference_path.read_bytes(),
                media_type="image/png",
                identifier="static_image",
            ),
        ],
        deps=Deps(
            parking_assessment=parking_assessment,
            system_prompt=system_prompt,
            video_start_time=video_start_time,
        ),
        model_settings=GoogleModelSettings(
            google_video_resolution=MediaResolution.MEDIA_RESOLUTION_HIGH
        ),
    )
    return result.output
