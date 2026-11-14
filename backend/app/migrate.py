import logging
from pathlib import Path

import psycopg

from app.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
# The API and the worker start together; the lock serialises their runs.
_LOCK_KEY = 0x6D696772

log = logging.getLogger(__name__)


def migrate(url: str | None = None) -> list[str]:
    """Apply each pending migration in its own transaction and return the filenames applied."""
    applied_now = []
    with psycopg.connect(url or settings.database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            log.info("applied migration %s", path.name)
            applied_now.append(path.name)
    return applied_now


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    applied = migrate()
    print(f"applied {len(applied)} migration(s)")
