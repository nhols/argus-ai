import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vid_analyser.db.types import ExecutionStatus, NotificationStatus, VideoUploadStatus


@dataclass(slots=True)
class ExecutionRecord:
    id: str
    created_at: str
    updated_at: str
    status: str
    error_message: str | None
    source: str
    input_video_s3_bucket: str | None
    input_video_s3_key: str | None
    input_video_filename: str | None
    input_video_content_type: str | None
    input_video_size_bytes: int | None
    video_upload_status: str | None
    video_upload_error: str | None
    notification_status: str | None
    notification_channel: str | None
    notification_target: str | None
    notification_sent_at: str | None
    notification_error: str | None
    event_type: str | None
    device_serial_number: str | None
    station_serial_number: str | None
    event_start_time: str | None
    event_end_time: str | None
    event_metadata_json: str
    config_snapshot_json: str | None
    analysis_result_json: str | None


class ExecutionRepository:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)

    def create_execution(
        self,
        *,
        execution_id: str,
        created_at: str,
        updated_at: str,
        status: ExecutionStatus,
        source: str,
        event_metadata: dict[str, Any],
        input_video_filename: str | None,
        input_video_content_type: str | None,
        input_video_size_bytes: int | None,
        device_serial_number: str | None,
        station_serial_number: str | None,
        event_start_time: str | None,
        event_end_time: str | None,
        video_upload_status: VideoUploadStatus | None = None,
        event_type: str | None = None,
        notification_status: NotificationStatus | None = None,
        notification_channel: str | None = None,
        notification_target: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO executions (
                    id, created_at, updated_at, status, error_message, source,
                    input_video_s3_bucket, input_video_s3_key, input_video_filename,
                    input_video_content_type, input_video_size_bytes, video_upload_status, video_upload_error,
                    notification_status, notification_channel, notification_target,
                    notification_sent_at, notification_error,
                    event_type, device_serial_number, station_serial_number,
                    event_start_time, event_end_time,
                    event_metadata_json, config_snapshot_json, analysis_result_json
                ) VALUES (?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    execution_id,
                    created_at,
                    updated_at,
                    status.value,
                    source,
                    input_video_filename,
                    input_video_content_type,
                    input_video_size_bytes,
                    video_upload_status.value if video_upload_status is not None else None,
                    notification_status.value if notification_status is not None else None,
                    notification_channel,
                    notification_target,
                    event_type,
                    device_serial_number,
                    station_serial_number,
                    event_start_time,
                    event_end_time,
                    _to_json(event_metadata),
                    _to_json(config_snapshot),
                ),
            )

    def update_execution(self, execution_id: str, *, updated_at: str, **fields: Any) -> None:
        if not fields:
            return

        serialised = {
            key: _serialise_field(key, value)
            for key, value in fields.items()
        }
        assignments = ", ".join(f"{key} = ?" for key in serialised)
        params = [*serialised.values(), updated_at, execution_id]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE executions SET {assignments}, updated_at = ? WHERE id = ?",
                params,
            )

    def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM executions WHERE id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:
            return None
        return ExecutionRecord(**dict(row))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _serialise_field(key: str, value: Any) -> Any:
    if key.endswith("_json") and value is not None:
        return _to_json(value)
    if isinstance(value, (ExecutionStatus, NotificationStatus, VideoUploadStatus)):
        return value.value
    return value


def _to_json(value: dict[str, Any] | list[Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)
