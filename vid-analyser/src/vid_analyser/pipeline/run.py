import logging
from datetime import UTC, datetime
from pathlib import Path

import logfire
from google.genai.types import MediaResolution
from opentelemetry.trace.span import format_span_id, format_trace_id
from pydantic_ai import BinaryContent
from pydantic_ai.models.google import GoogleModelSettings
from vid_analyser.agent.notifier import Deps as NotifierDeps
from vid_analyser.agent.notifier import NoNotification, notifier_agent
from vid_analyser.agent.vid_analyser import Deps as VidAnalysisDeps
from vid_analyser.agent.vid_analyser import vid_analyser_agent
from vid_analyser.config_schema import RunConfig
from vid_analyser.db import Database
from vid_analyser.notifications.telegram import TelegramNotificationService
from vid_analyser.overlay import generate_overlay_reference_frame, zone_descriptions

logger = logging.getLogger(__name__)


def _span_ids_from_logfire_span(span: object) -> tuple[str | None, str | None]:
    get_span_context = getattr(span, "get_span_context", None)
    if get_span_context is None:
        return None, None
    span_context = get_span_context()
    if not getattr(span_context, "is_valid", False):
        return None, None
    return (
        format_trace_id(span_context.trace_id),
        format_span_id(span_context.span_id),
    )


async def run(
    video_path: str | Path,
    config: RunConfig,
    content_type: str,
    *,
    db: Database | None = None,
    clip_start_time: datetime | None = None,
    clip_end_time: datetime | None = None,
):
    original_video_path = Path(video_path)
    cleanup_paths: list[Path] = []
    video_start_time = datetime.now(UTC)
    overlay_reference_frame_path: Path | None = None
    overlay_zones_info = (
        zone_descriptions(config.overlay.zones) if config.overlay and config.overlay.zones else None
    )

    logger.info("Pipeline run started video_path=%s", original_video_path)

    try:
        with logfire.span("video analysis pipeline", video_path=str(original_video_path)) as pipeline_span:
            logfire_trace_id, logfire_span_id = _span_ids_from_logfire_span(pipeline_span)

            if config.overlay is not None and config.overlay.zones:
                logger.info("Generating overlay reference frame zones count=%s", len(config.overlay.zones))
                overlay_reference_frame_path = generate_overlay_reference_frame(original_video_path, config.overlay.zones)
                cleanup_paths.append(overlay_reference_frame_path)
                logger.info("Overlay reference frame generated at: %s", overlay_reference_frame_path)

            analysis_inputs: list[str | BinaryContent] = [
                "Analyse this video.",
                BinaryContent(
                    original_video_path.read_bytes(),
                    media_type=content_type,
                    vendor_metadata={"fps": 5.0},
                )
            ]
            if overlay_reference_frame_path is not None:
                analysis_inputs.extend(
                    [
                        (
                            "File static_image is a static reference image taken from this video. "
                            "The overlay zones below relate to static_image. "
                            "Pay close attention to those zones when analysing static_image and the video.\n"
                            f"The overlay zones for file static_image are:\n{overlay_zones_info}"
                        ),
                        "This is file static_image from the video:",
                        BinaryContent(
                            overlay_reference_frame_path.read_bytes(),
                            media_type="image/png",
                            identifier="static_image",
                        ),
                    ]
                )

            analysis = await vid_analyser_agent.run(
                analysis_inputs,
                deps=VidAnalysisDeps(
                    video_path=original_video_path,
                    system_prompt=config.video_analyser_sys_prompt,
                    video_start_time=video_start_time,
                ),
                model_settings=GoogleModelSettings(
                    google_video_resolution=MediaResolution.MEDIA_RESOLUTION_HIGH
                ),
            )
            analysis_record = None
            if db is not None:
                analysis_record = await db.insert_analysis(
                    video_path=original_video_path,
                    result_json=analysis.output.model_dump_json(),
                    clip_start_time=clip_start_time,
                    clip_end_time=clip_end_time,
                    logfire_trace_id=logfire_trace_id,
                    logfire_span_id=logfire_span_id,
                )

            logger.info("Video analysis complete, passing result to notifier agent")

            noti_result = await notifier_agent.run(
                user_prompt=analysis.output.model_dump_json(indent=2),
                deps=NotifierDeps(
                    video_path=original_video_path,
                    vid_analysis_id=analysis_record.id if analysis_record is not None else None,
                    system_prompt=config.notifier_sys_prompt,
                    style_guide=config.notifier_style,
                    video_start_time=video_start_time,
                    notification_service=TelegramNotificationService() if config.telegram_chat_id else None,
                    db=db,
                    chat_id=config.telegram_chat_id,
                    get_bookings=config.get_bookings,
                    n_previous_messages=config.previous_messages_limit,
                ),
            )
            if isinstance(noti_result.output, NoNotification):
                logger.info("Agent opted not to send a notification, explanation: %s", noti_result.output.explanation)
            else:
                logger.info("Agent opted to send a notification, message: %s", noti_result.output)
            return analysis.output
    finally:
        for cleanup_path in cleanup_paths:
            cleanup_path.unlink(missing_ok=True)
