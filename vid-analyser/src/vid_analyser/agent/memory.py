from vid_analyser.db import Database

GLOBAL_AGENT_MEMORY_NAME = "global"


async def build_memory_instructions(
    *,
    db: Database | None,
    limit: int,
    decay_days: float,
) -> str | None:
    if db is None or limit <= 0:
        return None
    memories = await db.get_ranked_agent_memories(
        agent_name=GLOBAL_AGENT_MEMORY_NAME,
        limit=limit,
        decay_days=decay_days,
    )
    if not memories:
        return None
    return "\n".join(
        ["Here are some ranked memories you've noted during previous conversations:"]
        + [
            f"- weight={memory.weight} core={memory.is_core} created_at={memory.created_at}: {memory.memory_text}"
            for memory in memories
        ]
    )
