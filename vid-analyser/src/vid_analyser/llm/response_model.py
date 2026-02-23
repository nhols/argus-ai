from typing import Literal

from pydantic import BaseModel

ParkingSpotStatus = Literal["occupied", "vacant", "car entering", "car leaving", "unknown"]
IRMode = Literal["yes", "no", "unknown"]


class AnalyzeResponse(BaseModel):
    ir_mode: IRMode
    parking_spot_status: ParkingSpotStatus
    number_plate: str | None
    events_description: str
    message_for_user: str
    send_notification: bool
