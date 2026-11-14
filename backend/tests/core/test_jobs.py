from app import jobs


def expire_lease(conn, job_id):
    conn.execute(
        "UPDATE job SET updated_at = now() - %s - interval '1 minute' WHERE id = %s", (jobs.LEASE, job_id)
    )
    conn.commit()


def test_running_job_whose_lease_expired_is_claimed_again(conn):
    job_id = jobs.enqueue(conn, "noop")
    jobs.claim(conn)
    conn.commit()
    assert jobs.claim(conn) is None

    expire_lease(conn, job_id)
    reclaimed = jobs.claim(conn)
    assert (reclaimed.id, reclaimed.attempts) == (job_id, 2)


def test_expired_job_out_of_attempts_fails(conn):
    job_id = jobs.enqueue(conn, "noop")
    conn.execute("UPDATE job SET state = 'running', attempts = %s WHERE id = %s", (jobs.MAX_ATTEMPTS, job_id))
    expire_lease(conn, job_id)
    assert jobs.claim(conn) is None
    conn.commit()
    assert conn.execute("SELECT state, last_error FROM job").fetchone() == ("failed", "lease expired")
