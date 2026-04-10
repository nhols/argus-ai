from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from vid_analyser.db.database import Database
from vid_analyser.db.migrations import run_migrations


def build_session_factory(db_path: str | Path) -> async_sessionmaker[AsyncSession]:
    sqlite_path = Path(db_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{sqlite_path}", future=True)
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def init_database(db_path: str | Path) -> Database:
    session_factory = build_session_factory(db_path)
    async with session_factory.kw["bind"].begin() as conn:
        await conn.run_sync(run_migrations)
    return Database(session_factory)
