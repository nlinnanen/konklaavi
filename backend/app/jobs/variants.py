from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.jobs import register
from app.jobs.fetch import update_centroids

# Candidates share at least this many LSH bands; a match is confirmed at this estimated Jaccard.
MIN_SHARED_BANDS = 2
MIN_JACCARD = 0.5


@dataclass(frozen=True)
class Candidate:
    id: int
    jaccard: float
    variant_id: int | None
    first_seen: datetime


def jaccard(a: list[int], b: list[int]) -> float:
    """Estimated Jaccard similarity of two MinHash signatures: the share of equal values."""
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)


def confirmed_matches(conn: Connection, fingerprint_id: int, signature: list[int]) -> list[Candidate]:
    """Other fingerprints sharing enough bands whose signatures confirm the match, most similar first."""
    rows = conn.execute(
        """
        SELECT f.id, f.minhash, f.variant_id, f.first_seen
        FROM lsh_band mine
        JOIN lsh_band other ON other.band = mine.band AND other.hash = mine.hash
        JOIN fingerprint f ON f.id = other.fingerprint_id
        WHERE mine.fingerprint_id = %s AND other.fingerprint_id <> %s AND f.minhash IS NOT NULL
        GROUP BY f.id
        HAVING count(*) >= %s
        """,
        (fingerprint_id, fingerprint_id, MIN_SHARED_BANDS),
    )
    candidates = [
        Candidate(id_, jaccard(signature, minhash), variant_id, seen)
        for id_, minhash, variant_id, seen in rows
    ]
    return sorted(
        (c for c in candidates if c.jaccard >= MIN_JACCARD), key=lambda c: (-c.jaccard, c.first_seen, c.id)
    )


def is_boilerplate(conn: Connection, fingerprint_id: int) -> bool:
    """Whether the fingerprint was seen on at least two pages of one domain."""
    return (
        conn.execute(
            """
            SELECT 1 FROM observation o JOIN page p ON p.id = o.page_id
            WHERE o.fingerprint_id = %s
            GROUP BY p.domain_id HAVING count(DISTINCT o.page_id) >= 2
            LIMIT 1
            """,
            (fingerprint_id,),
        ).fetchone()
        is not None
    )


def match(conn: Connection, fingerprint_id: int) -> int | None:
    """Join the closest matching variant and link to the closest earlier copy; return a narrative it joined."""
    row = conn.execute(
        "SELECT minhash, variant_id, first_seen FROM fingerprint WHERE id = %s FOR UPDATE", (fingerprint_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return None
    signature, variant_id, first_seen = row
    matches = confirmed_matches(conn, fingerprint_id, signature)

    earlier = next((c for c in matches if (c.first_seen, c.id) < (first_seen, fingerprint_id)), None)
    if earlier is not None:
        conn.execute(
            """
            INSERT INTO edge (src_kind, src, dst_kind, dst, type, props)
            VALUES ('fingerprint', %s, 'fingerprint', %s, 'DERIVED_FROM', %s)
            ON CONFLICT DO NOTHING
            """,
            (
                fingerprint_id,
                earlier.id,
                Jsonb(
                    {
                        "jaccard": earlier.jaccard,
                        "dt_seconds": (first_seen - earlier.first_seen).total_seconds(),
                    }
                ),
            ),
        )

    target = next((c.variant_id for c in matches if c.variant_id is not None), None)
    # A later fetch may have found this fingerprint to be boilerplate before this job ran.
    if variant_id is not None or target is None or is_boilerplate(conn, fingerprint_id):
        return None
    conn.execute("UPDATE fingerprint SET variant_id = %s WHERE id = %s", (target, fingerprint_id))
    return conn.execute("SELECT narrative_id FROM variant WHERE id = %s", (target,)).fetchone()[0]


@register("match_variants")
def handle(conn: Connection, payload: dict) -> None:
    joined = {match(conn, fingerprint_id) for fingerprint_id in payload["fingerprint_ids"]}
    update_centroids(conn, joined - {None})
