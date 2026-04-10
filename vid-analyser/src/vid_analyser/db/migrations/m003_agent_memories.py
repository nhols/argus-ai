from sqlalchemy.engine import Connection


def _sqlite_table_exists(conn: Connection, table_name: str) -> bool:
    rows = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchall()
    return bool(rows)


def apply(conn: Connection) -> None:
    if _sqlite_table_exists(conn, "agent_memories"):
        return
    conn.exec_driver_sql(
        """
        CREATE TABLE agent_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at VARCHAR NOT NULL,
            agent_name VARCHAR NOT NULL,
            memory_text TEXT NOT NULL
        )
        """
    )
    conn.exec_driver_sql("CREATE INDEX ix_agent_memories_agent_name ON agent_memories (agent_name)")
