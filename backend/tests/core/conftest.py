import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client(conn):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def signed_in(conn):
    """Add an install linked to an account for the given email; returns the install id."""

    def add(email=None):
        install = uuid.uuid4()
        account = conn.execute(
            "INSERT INTO user_account (email) VALUES (%s) RETURNING id", (email or f"{install}@example.org",)
        ).fetchone()[0]
        conn.execute("INSERT INTO install (id, account_id) VALUES (%s, %s)", (install, account))
        conn.commit()
        return install

    return add


@pytest.fixture
def narrative(conn, signed_in):
    """Add a note narrative whose fingerprint holds the given sentence hashes, with one note; returns its id."""

    def add(
        hashes,
        content_hash="fp-1",
        moderation="visible",
        status="needs_more_ratings",
        classification="misleading",
    ):
        author = signed_in()
        narrative_id = conn.execute(
            "INSERT INTO narrative (title, claim, source, source_url, state)"
            " VALUES ('Sanctions destroy Europe', 'Sanctions destroy Europe.', 'note', 'https://example.com/a', 'active')"
            " RETURNING id"
        ).fetchone()[0]
        variant_id = conn.execute(
            "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative_id,)
        ).fetchone()[0]
        fingerprint_id = conn.execute(
            "INSERT INTO fingerprint (content_hash, content, variant_id) VALUES (%s, 'text', %s) RETURNING id",
            (content_hash, variant_id),
        ).fetchone()[0]
        for h in hashes:
            conn.execute("INSERT INTO sentence (hash, fingerprint_id) VALUES (%s, %s)", (h, fingerprint_id))
        conn.execute(
            "INSERT INTO note (narrative_id, author_install, account_id, classification, body, status, moderation)"
            " SELECT %s, i.id, i.account_id, %s, 'Context.', %s, %s FROM install i WHERE i.id = %s",
            (narrative_id, classification, status, moderation, author),
        )
        conn.commit()
        return narrative_id

    return add
