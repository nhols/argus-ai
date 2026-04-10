from sqlalchemy.engine import Connection

from vid_analyser.db.models import Base


def apply(conn: Connection) -> None:
    Base.metadata.create_all(conn)
