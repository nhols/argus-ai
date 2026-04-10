import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vid_analyser.db.models import (
    AgentMemoryRecord,
    ConfigUpdateRecord,
    SentNotificationRecord,
    TelegramChatMessageRecord,
    VidAnalysisRecord,
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def insert_config(self, *, config: dict[str, Any], source: str | None = None) -> ConfigUpdateRecord:
        record = ConfigUpdateRecord(created_at=utc_now_iso(), source=source, config_json=json.dumps(config))
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_latest_config(self) -> ConfigUpdateRecord | None:
        stmt = select(ConfigUpdateRecord).order_by(ConfigUpdateRecord.id.desc()).limit(1)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def insert_notification(
        self,
        *,
        video_path: str | Path,
        chat_id: str | None,
        message: str,
        vid_analysis_id: int | None = None,
    ) -> SentNotificationRecord:
        record = SentNotificationRecord(
            created_at=utc_now_iso(),
            video_path=str(video_path),
            chat_id=chat_id,
            vid_analysis_id=vid_analysis_id,
            message=message,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_recent_notifications(self, *, limit: int) -> list[SentNotificationRecord]:
        stmt = select(SentNotificationRecord).order_by(SentNotificationRecord.id.desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars())

    async def query_notifications(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        keyword: str | None = None,
        chat_id: str | None = None,
        limit: int = 10,
    ) -> list[SentNotificationRecord]:
        stmt = select(SentNotificationRecord)
        if chat_id is not None:
            stmt = stmt.where(SentNotificationRecord.chat_id == chat_id)
        if date_from is not None:
            stmt = stmt.where(SentNotificationRecord.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(SentNotificationRecord.created_at <= date_to)
        if keyword is not None and keyword.strip():
            stmt = stmt.where(SentNotificationRecord.message.ilike(f"%{keyword.strip()}%"))
        stmt = stmt.order_by(SentNotificationRecord.id.desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars())

    async def insert_analysis(
        self,
        *,
        video_path: str | Path,
        result_json: str,
        clip_start_time: datetime | None = None,
        clip_end_time: datetime | None = None,
        logfire_trace_id: str | None = None,
        logfire_span_id: str | None = None,
    ) -> VidAnalysisRecord:
        record = VidAnalysisRecord(
            created_at=utc_now_iso(),
            clip_start_time=clip_start_time,
            clip_end_time=clip_end_time,
            video_path=str(video_path),
            result_json=result_json,
            logfire_trace_id=logfire_trace_id,
            logfire_span_id=logfire_span_id,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def query_analyses(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[VidAnalysisRecord]:
        stmt = select(VidAnalysisRecord)
        if date_from is not None:
            stmt = stmt.where(VidAnalysisRecord.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(VidAnalysisRecord.created_at <= date_to)
        if keyword is not None and keyword.strip():
            stmt = stmt.where(VidAnalysisRecord.result_json.ilike(f"%{keyword.strip()}%"))
        stmt = stmt.order_by(VidAnalysisRecord.id.desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars())

    async def insert_telegram_chat_message(
        self,
        *,
        chat_id: str,
        text: str,
        direction: str,
        chat_type: str | None = None,
        message_id: str | None = None,
        update_id: str | None = None,
        sender_user_id: str | None = None,
        sender_username: str | None = None,
        sender_display_name: str | None = None,
    ) -> TelegramChatMessageRecord:
        record = TelegramChatMessageRecord(
            created_at=utc_now_iso(),
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=message_id,
            update_id=update_id,
            direction=direction,
            sender_user_id=sender_user_id,
            sender_username=sender_username,
            sender_display_name=sender_display_name,
            text=text,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def has_telegram_update(self, *, update_id: str) -> bool:
        stmt = select(TelegramChatMessageRecord.id).where(
            TelegramChatMessageRecord.update_id == update_id,
            TelegramChatMessageRecord.direction == "inbound",
        ).limit(1)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def get_recent_telegram_chat_messages(
        self,
        *,
        chat_id: str,
        limit: int,
    ) -> list[TelegramChatMessageRecord]:
        stmt = (
            select(TelegramChatMessageRecord)
            .where(TelegramChatMessageRecord.chat_id == chat_id)
            .order_by(TelegramChatMessageRecord.id.desc())
            .limit(limit)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars())

    async def insert_agent_memory(
        self,
        *,
        agent_name: str,
        memory_text: str,
        weight: float = 1.0,
        is_core: bool = False,
        created_at: str | None = None,
    ) -> AgentMemoryRecord:
        record = AgentMemoryRecord(
            created_at=created_at or utc_now_iso(),
            agent_name=agent_name,
            weight=weight,
            is_core=is_core,
            memory_text=memory_text,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_ranked_agent_memories(
        self,
        *,
        agent_name: str,
        limit: int,
        decay_days: float,
        now: datetime | None = None,
    ) -> list[AgentMemoryRecord]:
        stmt = (
            select(AgentMemoryRecord)
            .where(AgentMemoryRecord.agent_name == agent_name)
            .order_by(AgentMemoryRecord.id.desc())
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            records = list(result.scalars())
        if limit <= 0:
            return []
        current_time = now or datetime.now(UTC)
        decay_constant = math.log(2) / decay_days

        def rank(record: AgentMemoryRecord) -> float:
            if record.is_core:
                return record.weight
            created_at = datetime.fromisoformat(record.created_at)
            age_days = max((current_time - created_at).total_seconds() / 86400.0, 0.0)
            return record.weight * math.exp(-decay_constant * age_days)

        return sorted(records, key=rank, reverse=True)[:limit]
