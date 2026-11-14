import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls a paid external API; runs only when its key is set")


@pytest.fixture
def client(conn):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def vote(client):
    """Post a vote; returns the request body."""

    def post(verdict, hashes, content_hash=None, user=None, domain="example.com", excerpt="Some excerpt."):
        body = {
            "userId": str(user or uuid.uuid4()),
            "contentHash": content_hash or "c-" + "-".join(hashes),
            "sentenceHashes": hashes,
            "verdict": verdict,
            "url": f"https://{domain}/article",
            "domain": domain,
            "excerpt": excerpt,
            "createdAt": "2026-11-14T10:00:00Z",
        }
        response = client.post("/votes", json=body)
        assert response.status_code == 200, response.text
        return body

    return post


@pytest.fixture
def narrative(conn):
    """Add a narrative whose fingerprint contains the given sentence hashes; returns its id."""

    def add(hashes, state="active", content_hash="fp-1", verdict="misinformation"):
        narrative_id = conn.execute(
            "INSERT INTO narrative (title, verdict, techniques, explanation, source, source_url, state)"
            " VALUES ('Sanctions destroy Europe', %s, '{fear_appeal}', 'Why.', 'euvsdisinfo', 'https://case', %s)"
            " RETURNING id",
            (verdict, state),
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
        conn.commit()
        return narrative_id

    return add
