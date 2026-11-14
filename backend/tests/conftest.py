import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql


def _load_worktree_env():
    """Read the worktree's .env so host-side runs reach its stack's ports; exported variables win."""
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.is_file():
        return
    for line in env.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_worktree_env()

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or (
    f"postgresql://postgres:postgres@localhost:{os.environ.get('DB_PORT') or '5432'}/trust_test"
)
# Set before app.config is imported, so every app connection goes to the test database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app import db
from app.migrate import migrate


@pytest.fixture(scope="session")
def database_url():
    """A freshly created and migrated test database."""
    info = psycopg.conninfo.conninfo_to_dict(TEST_DATABASE_URL)
    name = info["dbname"]
    admin_url = psycopg.conninfo.make_conninfo(TEST_DATABASE_URL, dbname="postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    migrate(TEST_DATABASE_URL)
    yield TEST_DATABASE_URL
    db.close_pool()


@pytest.fixture
def conn(database_url):
    """A connection to the test database with every table emptied."""
    with psycopg.connect(database_url) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables"
                " WHERE schemaname = 'public' AND tablename <> 'schema_migrations'"
            )
        ]
        if tables:
            conn.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
        conn.commit()
        yield conn
