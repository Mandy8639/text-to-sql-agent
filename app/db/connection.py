"""Database engine creation."""

from sqlalchemy import Engine, create_engine


def make_engine(database_url: str, *, connect_timeout_s: int = 5) -> Engine:
    """Create a SQLAlchemy engine.

    `pool_pre_ping` drops dead connections (e.g. after a DB restart) instead of failing the
    next query. The connect timeout keeps tests and health checks from hanging when the
    database is down.
    """
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": connect_timeout_s},
    )
