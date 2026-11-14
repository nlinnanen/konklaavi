import numpy as np

from app.jobs.settle import handle


def add_narrative(conn, source, state, sentence_hash, source_url=None):
    narrative_id = conn.execute(
        "INSERT INTO narrative (title, source, state, source_url) VALUES ('Claim', %s, %s, %s) RETURNING id",
        (source, state, source_url),
    ).fetchone()[0]
    variant_id = conn.execute(
        "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative_id,)
    ).fetchone()[0]
    fingerprint_id = conn.execute(
        "INSERT INTO fingerprint (content_hash, content, variant_id) VALUES (%s, 'text', %s) RETURNING id",
        (f"block-{narrative_id}", variant_id),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sentence (hash, fingerprint_id) VALUES (%s, %s)", (sentence_hash, fingerprint_id)
    )
    return narrative_id


def add_claim_vector(conn, narrative_id, vector):
    unit = np.array(vector, dtype=float) / np.linalg.norm(vector)
    conn.execute(
        "INSERT INTO narrative_vector (narrative_id, kind, embedding) VALUES (%s, 'claim', %s::vector)",
        (narrative_id, str(unit.tolist())),
    )


def settlement(conn, narrative_id):
    return conn.execute(
        "SELECT state, settled_by, settled_at IS NOT NULL FROM narrative WHERE id = %s", (narrative_id,)
    ).fetchone()


def test_a_report_is_confirmed_when_a_matching_euvsdisinfo_case_appears_later(conn):
    report = add_narrative(conn, "report", "under_review", "shared", "https://example.org/post")
    handle(conn, {})
    assert settlement(conn, report) == ("under_review", None, False)

    case = add_narrative(conn, "euvsdisinfo", "active", "shared")
    handle(conn, {})
    assert settlement(conn, report) == ("active", case, True)


def test_a_report_is_confirmed_by_a_close_fact_check_vector(conn):
    report = add_narrative(conn, "report", "disputed", "report-only", "https://example.org/post")
    check = add_narrative(conn, "factcheck", "active", "check-only")
    far = add_narrative(conn, "factcheck", "active", "far-only")
    add_claim_vector(conn, report, [1.0, 0.1] + [0.0] * 382)
    add_claim_vector(conn, check, [1.0, 0.0] + [0.0] * 382)
    add_claim_vector(conn, far, [0.0, 1.0] + [0.0] * 382)
    handle(conn, {})
    assert settlement(conn, report) == ("active", check, True)
