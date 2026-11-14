import logging
import signal
import time
import traceback

from psycopg import Connection

from app.db import close_pool, connection
from app.jobs import HANDLERS, PERIODIC, claim, complete, enqueue, fail
from app.migrate import migrate

IDLE_SLEEP = 1.0
_PERIODIC_LOCK_KEY = 0x70657269

log = logging.getLogger("worker")


def enqueue_periodic(conn: Connection) -> None:
    """Enqueue each periodic kind that has a handler, unless one is pending or ran within its interval."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_PERIODIC_LOCK_KEY,))
    for kind, seconds in PERIODIC:
        if kind not in HANDLERS:
            continue
        recent = conn.execute(
            "SELECT 1 FROM job WHERE kind = %s"
            " AND (state IN ('queued', 'running') OR created_at > now() - make_interval(secs => %s))",
            (kind, seconds),
        ).fetchone()
        if recent is None:
            enqueue(conn, kind)


def run_one() -> bool:
    """Claim and run one due job. Returns False when none was due."""
    with connection() as conn:
        job = claim(conn)
    if job is None:
        return False
    handler = HANDLERS.get(job.kind)
    if handler is None:
        log.error("job %s: no handler for kind %r", job.id, job.kind)
        with connection() as conn:
            fail(conn, job, f"no handler for kind {job.kind!r}", retry=False)
        return True
    try:
        with connection() as conn:
            handler(conn, job.payload)
            complete(conn, job.id)
    except Exception:
        log.exception("job %s (%s) attempt %s failed", job.id, job.kind, job.attempts)
        with connection() as conn:
            fail(conn, job, traceback.format_exc(limit=5))
    else:
        log.info("job %s (%s) done", job.id, job.kind)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    migrate()
    log.info("worker started with handlers %s", sorted(HANDLERS))
    try:
        while not stopping:
            with connection() as conn:
                enqueue_periodic(conn)
            if not run_one():
                time.sleep(IDLE_SLEEP)
    finally:
        close_pool()


if __name__ == "__main__":
    main()
