import logging
from datetime import UTC, datetime
from pathlib import Path

import logfire
from opentelemetry.trace.span import format_span_id, format_trace_id
from vid_analyser.agent.analysis import analyse_video
from vid_analyser.agent.notifier import Deps as NotifierDeps
from vid_analyser.agent.notifier import NoNotification, notifier_agent
from vid_analyser.config_schema import RunConfig
from vid_analyser.db import Database
from vid_analyser.notifications.telegram import TelegramNotificationService

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
    video_start_time = clip_start_time or datetime.now(UTC)

    logger.info("Pipeline run started video_path=%s", original_video_path)

    with logfire.span(
        "video analysis pipeline", video_path=str(original_video_path)
    ) as pipeline_span:
        logfire_trace_id, logfire_span_id = _span_ids_from_logfire_span(pipeline_span)
        if db is not None:
            active_snooze = await db.get_active_vid_analyser_snooze()
            if active_snooze is not None:
                logger.info(
                    "Discarding video analysis while snoozed video_path=%s snooze_id=%s ends_at=%s reason=%s",
                    original_video_path,
                    active_snooze.id,
                    active_snooze.ends_at.isoformat(),
                    active_snooze.reason,
                )
                return None

        if config.overlay is None:
            raise ValueError("Video analysis requires a parking spot overlay.")
        analysis_output = await analyse_video(
            original_video_path,
            content_type,
            config.overlay.zones,
            scene_system_prompt=config.video_analyser_sys_prompt,
            parking_spot_system_prompt=config.parking_spot_sys_prompt,
            video_start_time=video_start_time,
        )
        analysis_record = None
        if db is not None:
            analysis_record = await db.insert_analysis(
                video_path=original_video_path,
                result_json=analysis_output.model_dump_json(),
                clip_start_time=clip_start_time,
                clip_end_time=clip_end_time,
                logfire_trace_id=logfire_trace_id,
                logfire_span_id=logfire_span_id,
            )

        logger.info("Video analysis complete, passing result to notifier agent")

        noti_result = await notifier_agent.run(
            user_prompt=analysis_output.model_dump_json(indent=2),
            deps=NotifierDeps(
                video_path=original_video_path,
                vid_analysis_id=analysis_record.id
                if analysis_record is not None
                else None,
                system_prompt=config.notifier_sys_prompt,
                video_start_time=video_start_time,
                notification_service=TelegramNotificationService()
                if config.telegram_chat_id
                else None,
                db=db,
                chat_id=config.telegram_chat_id,
                get_bookings=config.get_bookings,
                n_previous_messages=config.previous_messages_limit,
                agent_memory_limit=config.agent_memory_limit,
                agent_memory_decay_days=config.agent_memory_half_life_days,
            ),
        )
        if isinstance(noti_result.output, NoNotification):
            logger.info(
                "Agent opted not to send a notification, explanation: %s",
                noti_result.output.explanation,
            )
        else:
            logger.info(
                "Agent opted to send a notification, message: %s", noti_result.output
            )
        return analysis_output
