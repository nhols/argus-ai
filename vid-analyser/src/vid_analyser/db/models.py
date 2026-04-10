from datetime import UTC, datetime

from sqlalchemy import Integer, String, Text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AwareDateTime(TypeDecorator[datetime]):
    impl = String
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()

    def process_result_value(self, value: str | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)


class Base(DeclarativeBase):
    pass


class ConfigUpdateRecord(Base):
    __tablename__ = "config_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)


class SentNotificationRecord(Base):
    __tablename__ = "sent_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    video_path: Mapped[str] = mapped_column(String, nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String, nullable=True)
    vid_analysis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)


class VidAnalysisRecord(Base):
    __tablename__ = "vid_analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    clip_start_time: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    clip_end_time: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    video_path: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    logfire_trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    logfire_span_id: Mapped[str | None] = mapped_column(String, nullable=True)


class TelegramChatMessageRecord(Base):
    __tablename__ = "telegram_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    chat_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    chat_type: Mapped[str | None] = mapped_column(String, nullable=True)
    message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    update_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    sender_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sender_username: Mapped[str | None] = mapped_column(String, nullable=True)
    sender_display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class AgentMemoryRecord(Base):
    __tablename__ = "agent_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    agent_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    weight: Mapped[float] = mapped_column(nullable=False, default=1.0)
    is_core: Mapped[bool] = mapped_column(nullable=False, default=False)
    memory_text: Mapped[str] = mapped_column(Text, nullable=False)
