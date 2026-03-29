import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    message: Mapped[str] = mapped_column(Text, nullable=False)


class VidAnalysisRecord(Base):
    __tablename__ = "vid_analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    video_path: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_session_factory(db_path: str | Path) -> async_sessionmaker[AsyncSession]:
    sqlite_path = Path(db_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{sqlite_path}", future=True)
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def init_database(db_path: str | Path) -> async_sessionmaker[AsyncSession]:
    session_factory = build_session_factory(db_path)
    async with session_factory.kw["bind"].begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_factory


class ConfigUpdateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def insert(self, *, config: dict[str, Any], source: str | None = None) -> ConfigUpdateRecord:
        record = ConfigUpdateRecord(
            created_at=_utc_now_iso(),
            source=source,
            config_json=json.dumps(config),
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_latest(self) -> ConfigUpdateRecord | None:
        stmt = select(ConfigUpdateRecord).order_by(ConfigUpdateRecord.id.desc()).limit(1)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()


class SentNotificationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def insert(self, *, video_path: str | Path, chat_id: str | None, message: str) -> SentNotificationRecord:
        record = SentNotificationRecord(
            created_at=_utc_now_iso(),
            video_path=str(video_path),
            chat_id=chat_id,
            message=message,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def get_recent(self, *, limit: int) -> list[SentNotificationRecord]:
        stmt = select(SentNotificationRecord).order_by(SentNotificationRecord.id.desc()).limit(limit)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars())


class VidAnalysisRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def insert(self, *, video_path: str | Path, result_json: str) -> VidAnalysisRecord:
        record = VidAnalysisRecord(
            created_at=_utc_now_iso(),
            video_path=str(video_path),
            result_json=result_json,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record
