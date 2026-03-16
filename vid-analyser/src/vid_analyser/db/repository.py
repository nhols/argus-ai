import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from vid_analyser.db.types import ExecutionStatus, NotificationStatus, VideoUploadStatus


@dataclass(slots=True)
class ExecutionRecord:
    id: str
    created_at: str
    updated_at: str
    status: str
    error_message: str | None
    source: str
    video_storage_provider: str | None
    video_storage_path: str | None
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
    config_version_id: str | None
    event_metadata_json: str
    analysis_result_json: str | None


@dataclass(slots=True)
class ConfigVersionRecord:
    id: str
    created_at: str
    source: str | None
    config_json: str


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
        config_version_id: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO executions (
                    id, created_at, updated_at, status, error_message, source,
                    video_storage_provider, video_storage_path, input_video_filename,
                    input_video_content_type, input_video_size_bytes, video_upload_status, video_upload_error,
                    notification_status, notification_channel, notification_target,
                    notification_sent_at, notification_error,
                    event_type, device_serial_number, station_serial_number,
                    event_start_time, event_end_time, config_version_id,
                    event_metadata_json, analysis_result_json
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
                    config_version_id,
                    _to_json(event_metadata),
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

    def list_executions(self, *, limit: int = 100) -> list[ExecutionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM executions
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [ExecutionRecord(**dict(row)) for row in rows]

    def get_recent_notification_messages(self, *, limit: int) -> list[dict[str, str | None]]:
        if limit <= 0:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_start_time, analysis_result_json
                FROM executions
                WHERE analysis_result_json IS NOT NULL
                  AND notification_status = ?
                ORDER BY COALESCE(event_start_time, created_at) DESC
                LIMIT ?
                """,
                (NotificationStatus.SENT.value, limit),
            ).fetchall()

        messages: list[dict[str, str | None]] = []
        for row in reversed(rows):
            analysis_result_json = row["analysis_result_json"]
            if not analysis_result_json:
                continue
            analysis_result = json.loads(analysis_result_json)
            message_for_user = analysis_result.get("message_for_user")
            if not message_for_user:
                continue
            messages.append(
                {
                    "start_time": row["event_start_time"],
                    "message_for_user": message_for_user,
                }
            )
        return messages

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


class ConfigRepository:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)

    def get_latest_config(self) -> ConfigVersionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, created_at, source, config_json
                FROM config_versions
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return ConfigVersionRecord(**dict(row))

    def get_config(self, config_version_id: str) -> ConfigVersionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, created_at, source, config_json
                FROM config_versions
                WHERE id = ?
                """,
                (config_version_id,),
            ).fetchone()
        if row is None:
            return None
        return ConfigVersionRecord(**dict(row))

    def insert_config_version(
        self,
        *,
        config: dict[str, Any],
        created_at: str,
        source: str | None = None,
    ) -> ConfigVersionRecord:
        record = ConfigVersionRecord(
            id=str(uuid4()),
            created_at=created_at,
            source=source,
            config_json=_to_json(config) or "{}",
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO config_versions (id, created_at, source, config_json)
                VALUES (?, ?, ?, ?)
                """,
                (record.id, record.created_at, record.source, record.config_json),
            )
        return record

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
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
