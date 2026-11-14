from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection
from psycopg.types.json import Jsonb

from app import fpclient
from app.jobs import enqueue, register

# Candidates share at least this many LSH bands; a match is confirmed at this estimated Jaccard.
MIN_SHARED_BANDS = 2
MIN_JACCARD = 0.5
# A narrative's vectors hold at most this many of its attached blocks besides its claim.
MAX_BLOCK_VECTORS = 5
# Narrative sources that label whole pages of a source, such as a documented network, rather than one claim.
SOURCE_LEVEL = ("network",)


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


def narrative_text(conn: Connection, narrative_id: int) -> str | None:
    """The narrative's title and claim, or None when there is no such narrative."""
    row = conn.execute("SELECT title, claim FROM narrative WHERE id = %s", (narrative_id,)).fetchone()
    return None if row is None else "\n".join(part for part in row if part)


def central_blocks(conn: Connection, narrative_ids: list[int]) -> dict[int, set[int]]:
    """Per narrative, its MAX_BLOCK_VECTORS attached prose, non-boilerplate blocks closest to their mean."""
    attached = [
        row[0]
        for row in conn.execute(
            "SELECT f.id FROM fingerprint f JOIN variant v ON v.id = f.variant_id"
            " WHERE v.narrative_id = ANY(%s) AND f.prose AND f.embedding IS NOT NULL",
            (narrative_ids,),
        )
    ]
    keep = list(set(attached) - boilerplate(conn, attached))
    rows = conn.execute(
        """
        WITH block AS (
          SELECT v.narrative_id, f.id, f.embedding FROM fingerprint f JOIN variant v ON v.id = f.variant_id
          WHERE f.id = ANY(%s)
        ), centre AS (
          SELECT narrative_id, avg(embedding) AS mean FROM block GROUP BY narrative_id
        )
        SELECT narrative_id, id FROM (
          SELECT b.narrative_id, b.id,
                 row_number() OVER (PARTITION BY b.narrative_id ORDER BY b.embedding <=> c.mean, b.id) AS rank
          FROM block b JOIN centre c USING (narrative_id)
        ) ranked
        WHERE rank <= %s
        """,
        (keep, MAX_BLOCK_VECTORS),
    )
    central = defaultdict(set)
    for narrative_id, fingerprint_id in rows:
        central[narrative_id].add(fingerprint_id)
    return central


def refresh_narrative_vectors(conn: Connection, narrative_ids: Iterable[int]) -> None:
    """Bring each narrative's vectors up to date: its central blocks, and its claim when it has none.

    A narrative whose central blocks are unchanged keeps its rows, so GET /index sends it no delta.
    """
    # A source-level narrative holds no single claim, so it gets no vectors for narrative-level matching.
    ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM narrative WHERE id = ANY(%s) AND source <> ALL(%s) ORDER BY id",
            (sorted(set(narrative_ids)), list(SOURCE_LEVEL)),
        )
    ]
    if not ids:
        return
    central = central_blocks(conn, ids)
    stored = defaultdict(set)
    for narrative_id, fingerprint_id in conn.execute(
        "SELECT narrative_id, fingerprint_id FROM narrative_vector WHERE narrative_id = ANY(%s) AND kind = 'block'",
        (ids,),
    ):
        stored[narrative_id].add(fingerprint_id)
    changed = [id_ for id_ in ids if central[id_] != stored[id_]]
    if changed:
        conn.execute(
            "DELETE FROM narrative_vector WHERE narrative_id = ANY(%s) AND kind = 'block'", (changed,)
        )
        conn.execute(
            """
            INSERT INTO narrative_vector (narrative_id, kind, fingerprint_id, embedding)
            SELECT v.narrative_id, 'block', f.id, f.embedding FROM fingerprint f JOIN variant v ON v.id = f.variant_id
            WHERE v.narrative_id = ANY(%s) AND f.id = ANY(%s)
            ON CONFLICT DO NOTHING
            """,
            (changed, [fingerprint_id for id_ in changed for fingerprint_id in central[id_]]),
        )

    without_claim = [
        row[0]
        for row in conn.execute(
            "SELECT n.id FROM narrative n WHERE n.id = ANY(%s) AND NOT EXISTS ("
            " SELECT 1 FROM narrative_vector nv WHERE nv.narrative_id = n.id AND nv.kind = 'claim'"
            ") ORDER BY n.id",
            (ids,),
        )
    ]
    if not without_claim:
        return
    texts = [narrative_text(conn, id_) for id_ in without_claim]
    embedded = fpclient.fingerprint(texts, embed=True)["blocks"]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO narrative_vector (narrative_id, kind, embedding) VALUES (%s, 'claim', %s::vector)"
            " ON CONFLICT DO NOTHING",
            [
                (id_, str(block["embedding"]))
                for id_, block in zip(without_claim, embedded, strict=True)
                if block["embedding"] is not None
            ],
        )


@register("narrative_vectors")
def handle_narrative_vectors(conn: Connection, payload: dict) -> None:
    """Refresh the given narratives' vectors, or every narrative's that is not retired or rejected."""
    narrative_ids = payload.get("narrative_ids")
    if narrative_ids is None:
        narrative_ids = [
            row[0]
            for row in conn.execute("SELECT id FROM narrative WHERE state NOT IN ('retired', 'rejected')")
        ]
    refresh_narrative_vectors(conn, narrative_ids)


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
    joined = {match(conn, fingerprint_id) for fingerprint_id in payload["fingerprint_ids"]} - {None}
    update_centroids(conn, joined)
    if joined:
        enqueue(conn, "narrative_vectors", {"narrative_ids": sorted(joined)})
