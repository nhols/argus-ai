import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.db import init_database


def test_ranked_agent_memories_decay_and_core(tmp_path):
    async def _run():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        await db.insert_agent_memory(
            agent_name="global",
            memory_text="old weak memory",
            weight=10.0,
            is_core=False,
            created_at="2026-04-01T00:00:00+00:00",
        )
        await db.insert_agent_memory(
            agent_name="global",
            memory_text="recent memory",
            weight=2.0,
            is_core=False,
            created_at="2026-04-10T11:00:00+00:00",
        )
        await db.insert_agent_memory(
            agent_name="global",
            memory_text="core memory",
            weight=3.0,
            is_core=True,
            created_at="2026-03-01T00:00:00+00:00",
        )
        return await db.get_ranked_agent_memories(
            agent_name="global",
            limit=2,
            decay_days=1.0,
            now=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
        )

    memories = asyncio.run(_run())
    assert [memory.memory_text for memory in memories] == ["core memory", "recent memory"]
