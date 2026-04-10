from sqlalchemy.engine import Connection


def _sqlite_column_exists(conn: Connection, table_name: str, column_name: str) -> bool:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def _ensure_sqlite_column(conn: Connection, table_name: str, column_name: str, column_sql: str) -> None:
    if _sqlite_column_exists(conn, table_name, column_name):
        return
    conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def apply(conn: Connection) -> None:
    _ensure_sqlite_column(conn, "agent_memories", "weight", "FLOAT NOT NULL DEFAULT 1.0")
    _ensure_sqlite_column(conn, "agent_memories", "is_core", "BOOLEAN NOT NULL DEFAULT 0")
