"""Settle report narratives against the sources that count as truth.

An open report (state under_review or disputed) is rejected when the page it was reported on is a reliable
outlet's. Otherwise it is confirmed when an active EUvsDisinfo or fact-check narrative holds its text, by a
shared sentence or a near-duplicate block, or has a vector within NARRATIVE_MATCH_THRESHOLD cosine of one
of its vectors.
"""

from psycopg import Connection

from app.config import settings
from app.jobs import register
from app.jobs.variants import near_duplicates
from app.reliable import is_reliable_url

SETTLING_SOURCES = ["euvsdisinfo", "factcheck"]
OPEN_STATES = ["under_review", "disputed"]
SETTLER = "n.source = ANY(%(sources)s) AND n.state = 'active'"


def text_match(conn: Connection, narrative_id: int) -> int | None:
    """A settled narrative holding a sentence of the report or a near-duplicate of one of its blocks."""
    own = [
        row[0]
        for row in conn.execute(
            "SELECT f.id FROM fingerprint f JOIN variant v ON v.id = f.variant_id WHERE v.narrative_id = %s",
            (narrative_id,),
        )
    ]
    if not own:
        return None
    row = conn.execute(
        f"""
        SELECT n.id FROM narrative n
        JOIN variant v ON v.narrative_id = n.id JOIN fingerprint f ON f.variant_id = v.id
        WHERE {SETTLER} AND (
          f.id = ANY(%(near)s) OR EXISTS (
            SELECT 1 FROM sentence mine JOIN sentence theirs ON theirs.hash = mine.hash
            WHERE mine.fingerprint_id = ANY(%(own)s) AND theirs.fingerprint_id = f.id
          )
        )
        ORDER BY n.id LIMIT 1
        """,
        {"sources": SETTLING_SOURCES, "near": list(near_duplicates(conn, own)), "own": own},
    ).fetchone()
    return row[0] if row else None


def vector_match(conn: Connection, narrative_id: int) -> int | None:
    """The settled narrative with the closest vector to the report's, when within the match threshold."""
    row = conn.execute(
        f"""
        SELECT closest.narrative_id FROM narrative_vector mine
        CROSS JOIN LATERAL (
          SELECT nv.narrative_id, nv.embedding <=> mine.embedding AS distance
          FROM narrative_vector nv JOIN narrative n ON n.id = nv.narrative_id
          WHERE {SETTLER}
          ORDER BY distance LIMIT 1
        ) closest
        WHERE mine.narrative_id = %(id)s AND closest.distance <= 1 - %(threshold)s
        ORDER BY closest.distance LIMIT 1
        """,
        {"sources": SETTLING_SOURCES, "id": narrative_id, "threshold": settings.narrative_match_threshold},
    ).fetchone()
    return row[0] if row else None


def settle(conn: Connection) -> None:
    reports = conn.execute(
        "SELECT id, source_url FROM narrative WHERE source = 'report' AND state = ANY(%s) ORDER BY id",
        (OPEN_STATES,),
    ).fetchall()
    for narrative_id, url in reports:
        if url and is_reliable_url(url):
            conn.execute(
                "UPDATE narrative SET state = 'retired', settled_at = now(), updated_at = now() WHERE id = %s",
                (narrative_id,),
            )
            continue
        settler = text_match(conn, narrative_id) or vector_match(conn, narrative_id)
        if settler is not None:
            conn.execute(
                "UPDATE narrative SET state = 'active', settled_by = %s, settled_at = now(), updated_at = now()"
                " WHERE id = %s",
                (settler, narrative_id),
            )
    # Votes retire reports too; their settlement time is when they were retired.
    conn.execute(
        "UPDATE narrative SET settled_at = updated_at"
        " WHERE source = 'report' AND state = 'retired' AND settled_at IS NULL"
    )


@register("settle_reports")
def handle(conn: Connection, payload: dict) -> None:
    settle(conn)
