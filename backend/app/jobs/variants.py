from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.jobs import register

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


# Each given fingerprint paired with itself and with its near-duplicates: fingerprints with the same
# dateless text, and confirmed LSH candidates.
NEAR_DUPLICATES = """
    SELECT id, id AS dup FROM fingerprint WHERE id = ANY(%(ids)s)
    UNION
    SELECT f.id, d.id FROM fingerprint f JOIN fingerprint d ON d.dateless_hash = f.dateless_hash
    WHERE f.id = ANY(%(ids)s) AND f.prose AND d.prose
    UNION
    SELECT pair.id, pair.dup FROM (
      SELECT mine.fingerprint_id AS id, other.fingerprint_id AS dup
      FROM lsh_band mine JOIN lsh_band other ON other.band = mine.band AND other.hash = mine.hash
      WHERE mine.fingerprint_id = ANY(%(ids)s) AND other.fingerprint_id <> mine.fingerprint_id
      GROUP BY 1, 2 HAVING count(*) >= %(bands)s
    ) pair
    JOIN fingerprint a ON a.id = pair.id JOIN fingerprint b ON b.id = pair.dup
    WHERE (SELECT count(*) FROM unnest(a.minhash, b.minhash) m(x, y) WHERE x = y)
      >= %(jaccard)s * cardinality(a.minhash)
"""


def near_duplicates(conn: Connection, fingerprint_ids: Iterable[int]) -> set[int]:
    """The fingerprints and their near-duplicates."""
    params = {"ids": list(fingerprint_ids), "bands": MIN_SHARED_BANDS, "jaccard": MIN_JACCARD}
    return {row[0] for row in conn.execute(f"SELECT dup FROM ({NEAR_DUPLICATES}) near", params)}


def boilerplate(conn: Connection, fingerprint_ids: Iterable[int]) -> set[int]:
    """The fingerprints that are not narrative content.

    That is text that is not prose (see migration 200), or text whose near-duplicates appear on 2+ pages
    of a domain the text itself appears on.
    """
    params = {"ids": list(fingerprint_ids), "bands": MIN_SHARED_BANDS, "jaccard": MIN_JACCARD}
    rows = conn.execute(
        f"""
        WITH near AS ({NEAR_DUPLICATES})
        SELECT near.id FROM near
        JOIN observation o ON o.fingerprint_id = near.dup
        JOIN page p ON p.id = o.page_id
        WHERE p.domain_id IN (
          SELECT own.domain_id FROM observation mine JOIN page own ON own.id = mine.page_id
          WHERE mine.fingerprint_id = near.id
        )
        GROUP BY near.id, p.domain_id
        HAVING count(DISTINCT o.page_id) >= 2
        UNION
        SELECT id FROM fingerprint WHERE id = ANY(%(ids)s) AND NOT prose
        """,
        params,
    )
    return {row[0] for row in rows}


def is_boilerplate(conn: Connection, fingerprint_id: int) -> bool:
    return fingerprint_id in boilerplate(conn, [fingerprint_id])


def update_centroids(conn: Connection, narrative_ids: set[int]) -> None:
    """Set each narrative's centroid to the mean embedding of its fingerprints."""
    conn.execute(
        """
        UPDATE narrative n SET centroid = (
          SELECT avg(f.embedding) FROM fingerprint f JOIN variant v ON v.id = f.variant_id
          WHERE v.narrative_id = n.id
        )
        WHERE n.id = ANY(%s)
        """,
        (list(narrative_ids),),
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
