from sqlalchemy.engine import Connection


def apply(conn: Connection) -> None:
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS vid_analyser_snoozes (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            created_at VARCHAR NOT NULL,
            starts_at VARCHAR NOT NULL,
            ends_at VARCHAR NOT NULL,
            created_by VARCHAR,
            reason TEXT,
            cancelled_at VARCHAR,
            cancelled_by VARCHAR
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_vid_analyser_snoozes_starts_at "
        "ON vid_analyser_snoozes (starts_at)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_vid_analyser_snoozes_ends_at "
        "ON vid_analyser_snoozes (ends_at)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_vid_analyser_snoozes_cancelled_at "
        "ON vid_analyser_snoozes (cancelled_at)"
    )
