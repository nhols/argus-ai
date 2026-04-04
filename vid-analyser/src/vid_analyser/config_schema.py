from pydantic import BaseModel, Field

from vid_analyser.overlay_schema import ZoneDefinition


class OverlayConfig(BaseModel):
    zones: list[ZoneDefinition] = Field(default_factory=list)


class RunConfig(BaseModel):
    overlay: OverlayConfig | None = None
    video_analyser_sys_prompt: str | None = None
    notifier_sys_prompt: str | None = None
    notifier_style: str | None = None
    telegram_chat_id: str | None = None
    previous_messages_limit: int = 10
    get_bookings: bool = False
