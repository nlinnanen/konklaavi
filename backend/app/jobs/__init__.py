from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg import Connection
from psycopg.types.json import Jsonb

Handler = Callable[[Connection, dict], None]

HANDLERS: dict[str, Handler] = {}
# (kind, interval in seconds) for jobs the worker enqueues on a schedule.
PERIODIC: list[tuple[str, int]] = [("cluster", 300)]
MAX_ATTEMPTS = 5


def register(kind: str) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        if kind in HANDLERS:
            raise ValueError(f"job kind {kind!r} already has a handler")
        HANDLERS[kind] = handler
        return handler

    return decorator


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    payload: dict
    attempts: int


def enqueue(
    conn: Connection, kind: str, payload: dict | None = None, run_after: datetime | None = None
) -> int:
    row = conn.execute(
        "INSERT INTO job (kind, payload, run_after) VALUES (%s, %s, coalesce(%s, now())) RETURNING id",
        (kind, Jsonb(payload or {}), run_after),
    ).fetchone()
    return row[0]


def claim(conn: Connection) -> Job | None:
    """Mark the next due job running and count the attempt. Commit before running the handler."""
    row = conn.execute(
        """
        UPDATE job SET state = 'running', attempts = attempts + 1, updated_at = now()
        WHERE id = (
          SELECT id FROM job
          WHERE state = 'queued' AND run_after <= now()
          ORDER BY run_after, id
          LIMIT 1
          FOR UPDATE SKIP LOCKED
        )
        RETURNING id, kind, payload, attempts
        """
    ).fetchone()
    return Job(*row) if row else None


def complete(conn: Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE job SET state = 'done', last_error = NULL, updated_at = now() WHERE id = %s", (job_id,)
    )


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=30 * 2 ** (attempts - 1))


def fail(conn: Connection, job: Job, error: str, retry: bool = True) -> None:
    """Requeue the job after a backoff, or mark it failed once it is out of attempts."""
    if retry and job.attempts < MAX_ATTEMPTS:
        conn.execute(
            "UPDATE job SET state = 'queued', run_after = now() + %s, last_error = %s, updated_at = now()"
            " WHERE id = %s",
            (backoff(job.attempts), error, job.id),
        )
    else:
        conn.execute(
            "UPDATE job SET state = 'failed', last_error = %s, updated_at = now() WHERE id = %s",
            (error, job.id),
        )


# Owner modules register their handlers on import.
from app.jobs import analyze, cluster, fetch, variants  # noqa: F401
