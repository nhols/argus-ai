from sqlalchemy.engine import Connection


def apply(conn: Connection) -> None:
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS operator_notes (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            created_at VARCHAR NOT NULL,
            expires_at VARCHAR NOT NULL,
            created_by VARCHAR,
            note_text TEXT NOT NULL
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_operator_notes_expires_at "
        "ON operator_notes (expires_at)"
    )
