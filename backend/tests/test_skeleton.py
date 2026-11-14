import psycopg
import pytest
from fastapi.testclient import TestClient

from app import jobs, worker
from app.api.main import app
from app.migrate import migrate


@pytest.fixture
def handlers(monkeypatch):
    registry = dict(jobs.HANDLERS)
    monkeypatch.setattr(jobs, "HANDLERS", registry)
    monkeypatch.setattr(worker, "HANDLERS", registry)
    return registry


def job_row(conn, job_id):
    conn.commit()
    return conn.execute("SELECT state, attempts, last_error FROM job WHERE id = %s", (job_id,)).fetchone()


def test_migrations_apply_once(database_url, conn):
    count = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    assert migrate(database_url) == []
    assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == count > 0


def test_concurrent_claimers_get_a_job_once(database_url, conn):
    job_id = jobs.enqueue(conn, "noop")
    conn.commit()
    with psycopg.connect(database_url) as first, psycopg.connect(database_url) as second:
        claimed = jobs.claim(first)
        assert jobs.claim(second) is None
        first.commit()
        assert jobs.claim(second) is None
    assert claimed.id == job_id
    assert job_row(conn, job_id)[:2] == ("running", 1)


def test_note_jobs_are_claimed_before_page_jobs_queued_earlier(conn):
    jobs.enqueue(conn, "fetch", {"url": "https://example.com/a"})
    score_id = jobs.enqueue(conn, "score_notes")
    conn.commit()
    assert jobs.claim(conn).id == score_id


def test_failing_handler_retries_then_fails(conn, handlers):
    @jobs.register("boom")
    def boom(_conn, payload):
        raise RuntimeError(payload["why"])

    job_id = jobs.enqueue(conn, "boom", {"why": "broken"})
    conn.commit()
    for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
        assert worker.run_one()
        state, attempts, error = job_row(conn, job_id)
        assert attempts == attempt
        assert "broken" in error
        if attempt < jobs.MAX_ATTEMPTS:
            assert state == "queued"
            conn.execute("UPDATE job SET run_after = now() WHERE id = %s", (job_id,))
            conn.commit()
    assert state == "failed"
    assert not worker.run_one()


def test_unknown_kind_fails_without_retry(conn):
    job_id = jobs.enqueue(conn, "no-such-kind")
    conn.commit()
    assert worker.run_one()
    assert job_row(conn, job_id)[:2] == ("failed", 1)


def test_periodic_job_is_not_duplicated(conn, handlers):
    handlers["cluster"] = lambda _conn, _payload: None
    worker.enqueue_periodic(conn)
    worker.enqueue_periodic(conn)
    conn.commit()
    assert conn.execute("SELECT count(*) FROM job WHERE kind = 'cluster'").fetchone()[0] == 1


def test_health(database_url):
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": "dev"}
