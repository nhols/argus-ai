from sqlalchemy.engine import Connection

from vid_analyser.db.migrations.m001_initial_schema import apply as apply_m001_initial_schema
from vid_analyser.db.migrations.m002_analysis_links_and_logfire import apply as apply_m002_analysis_links_and_logfire
from vid_analyser.db.migrations.m003_agent_memories import apply as apply_m003_agent_memories
from vid_analyser.db.migrations.m004_agent_memory_weights import apply as apply_m004_agent_memory_weights


def run_migrations(conn: Connection) -> None:
    apply_m001_initial_schema(conn)
    apply_m002_analysis_links_and_logfire(conn)
    apply_m003_agent_memories(conn)
    apply_m004_agent_memory_weights(conn)
