from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from vid_analyser.agent.retry import create_google_retry_model
from vid_analyser.agent.utils import get_timestamps

DEFAULT_SYS_PROMT = """
You analyse short security-camera videos.

Report only what is directly visible in the video. Do not guess details that are not supported by the footage.

Prefer conservative answers when visibility is poor, the scene is ambiguous, or an object is only partially visible.
"""

ParkingSpotStatus = Literal["occupied", "vacant", "car entering", "car leaving", "unknown"]


class VidAnalysis(BaseModel):
    """
    Structured result describing the visible parking-state and notable events in a video clip.

    Field descriptions are as follows:
    - `ir_mode`: `true` if infrared or night-vision mode is clearly active, otherwise `false`.
    - `parking_spot_status`: one of `occupied`, `vacant`, `car entering`, `car leaving`, or `unknown`.
    - `number_plate`: the number plate of the car parked in the user's parking spot if it is clearly readable, otherwise `null`.
    - `events_description`: a detailed factual summary of the relevant activity in the clip.
    """

    ir_mode: bool
    parking_spot_status: ParkingSpotStatus
    number_plate: str | None
    events_description: str


@dataclass
class Deps:
    video_path: Path
    system_prompt: str | None
    video_start_time: datetime


vid_analyser_agent = Agent(
    model=create_google_retry_model("gemini-3.1-flash-lite-preview"),
    output_type=VidAnalysis,
    deps_type=Deps,
)


@vid_analyser_agent.instructions
async def set_timestamps(ctx: RunContext[Deps]):
    get_timestamps(ctx.deps.video_start_time)


@vid_analyser_agent.instructions
async def get_instructions(ctx: RunContext[Deps]) -> str:
    return ctx.deps.system_prompt or DEFAULT_SYS_PROMT
