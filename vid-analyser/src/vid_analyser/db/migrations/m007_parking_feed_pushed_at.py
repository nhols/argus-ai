from sqlalchemy.engine import Connection


def apply(conn: Connection) -> None:
    columns = conn.exec_driver_sql("PRAGMA table_info(vid_analysis_results)").fetchall()
    if any(column[1] == "parking_feed_pushed_at" for column in columns):
        return
    conn.exec_driver_sql(
        "ALTER TABLE vid_analysis_results ADD COLUMN parking_feed_pushed_at VARCHAR"
    )
