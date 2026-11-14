from collections.abc import Iterator
from contextlib import contextmanager

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: ConnectionPool | None = None


def open_pool(url: str | None = None) -> ConnectionPool:
    """Open the process-wide pool. Run migrations first: registering pgvector needs the extension."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(url or settings.database_url, configure=register_vector, min_size=1, open=True)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[Connection]:
    """A pooled connection; the transaction commits on normal exit and rolls back on an exception."""
    with open_pool().connection() as conn:
        yield conn
