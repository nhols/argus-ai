import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import BinaryContent
from vid_analyser.agent.notifier import Deps as NotifierDeps
from vid_analyser.agent.notifier import NoNotification, notifier_agent
from vid_analyser.agent.vid_analyser import Deps as VidAnalysisDeps
from vid_analyser.agent.vid_analyser import vid_analyser_agent
from vid_analyser.notifications.telegram import TelegramNotificationService
from vid_analyser.overlay import ZoneDefinition, overlay_zones

logger = logging.getLogger(__name__)


class OverlayConfig(BaseModel):
    zones: list[ZoneDefinition] = Field(default_factory=list)


class RunConfig(BaseModel):
    overlay: OverlayConfig | None = None
    video_analyser_sys_prompt: str | None = None
    notifier_sys_prompt: str | None = None
    telegram_chat_id: str | None = None
    previous_messages_limit: int = 10
    get_bookings: bool = False


async def run(video_path: str | Path, config: RunConfig, content_type: str) -> None:
    original_video_path = Path(video_path)
    effective_video_path = original_video_path
    cleanup_paths: list[Path] = []
    video_start_time = datetime.now(UTC)

    logger.info("Pipeline run started video_path=%s config=%s", effective_video_path, config)

    try:
        if config.overlay is not None and config.overlay.zones:
            logger.info("Applying overlay zones count=%s", len(config.overlay.zones))
            effective_video_path = overlay_zones(effective_video_path, config.overlay.zones)
            if effective_video_path != original_video_path:
                cleanup_paths.append(effective_video_path)
            logger.info("Overlay applied, video saved to: %s", effective_video_path)

        analysis = await vid_analyser_agent.run(
            ["Analyse this video", BinaryContent(effective_video_path.read_bytes(), media_type=content_type)],
            deps=VidAnalysisDeps(
                video_path=effective_video_path,
                system_prompt=config.video_analyser_sys_prompt,
                video_start_time=video_start_time,
            ),
        )

        logger.info("Video analysis complete, passing result to notifier agent")

        noti_result = await notifier_agent.run(
            user_prompt=analysis.output.model_dump_json(indent=2),
            deps=NotifierDeps(
                video_path=effective_video_path,
                system_prompt=config.notifier_sys_prompt,
                video_start_time=video_start_time,
                notification_service=TelegramNotificationService(),
                chat_id=config.telegram_chat_id,
                get_bookings=config.get_bookings,
                n_previous_messages=config.previous_messages_limit,
            ),
        )
        if isinstance(noti_result, NoNotification):
            logger.info("Agent opted not to send a notification, explanation: %s", noti_result.explanation)
        else:
            logger.info("Agent opted to send a notification, message: %s", noti_result)
    finally:
        for cleanup_path in cleanup_paths:
            cleanup_path.unlink(missing_ok=True)
