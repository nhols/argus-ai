from pydantic import BaseModel
from vid_analyser.llm.response_model import IRMode, ParkingSpotStatus


class Golden(BaseModel):
    ir_mode: IRMode
    parking_spot_status: ParkingSpotStatus
    number_plate: str | None
    event_checklist: list[str]
    send_notification: bool
    people: list[str]


class TestCase(BaseModel):
    video_path: str
    video_hash: str
    golden: Golden
