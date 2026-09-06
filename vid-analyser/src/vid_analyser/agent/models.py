from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_GOOGLE_MODEL = "gemini-3.1-flash-lite"
ParkingSpotStatus = Literal["occupied", "vacant", "car entering", "car leaving", "unknown"]


class ParkingSpotAssessment(BaseModel):
    parking_spot_status: ParkingSpotStatus
    number_plate: str | None
    vehicle_description: str | None = Field(
        default=None,
        description=(
            "A confidence-calibrated description of the relevant vehicle's apparent "
            "type, make, model, and colour when the imagery is not IR; null when no "
            "vehicle is relevant or visible."
        ),
    )


class SceneAnalysis(BaseModel):
    ir_mode: bool
    events_description: str


class VidAnalysis(BaseModel):
    ir_mode: bool
    parking_spot_status: ParkingSpotStatus
    number_plate: str | None
    vehicle_description: str | None = None
    events_description: str
