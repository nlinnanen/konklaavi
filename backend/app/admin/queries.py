"""What the admin pages read."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import numpy as np
from psycopg import Connection
from psycopg.rows import dict_row

from app import trust
from app.config import settings
from app.demo.seed_raters import URL_PREFIX as DEMO_SEED
from app.jobs.variants import MIN_JACCARD

STATES = ("misleading", "controversial", "context", "needs_ratings", "tracked")
SORTS = {
    "last_seen": "last_seen DESC NULLS LAST",
    "sites": "sites DESC",
    "notes": "notes DESC",
    "members": "members DESC",
    "created": "n.created_at DESC",
}
MAX_ROWS = 200
MAX_MEMBERS = 500

# A narrative's display state follows its visible notes as display_state in app/api/narratives.py does,
# with its counts of notes, members and spread. `demo` marks the narratives the demo seed wrote.
NARRATIVES = f"""
WITH notes AS (
  SELECT narrative_id,
         CASE
           WHEN bool_or(visible AND status = 'helpful' AND classification = 'misleading') THEN 'misleading'
           WHEN bool_or(visible AND status = 'helpful') THEN 'context'
           WHEN bool_or(visible AND status = 'controversial') THEN 'controversial'
           WHEN bool_or(visible AND status = 'needs_more_ratings') THEN 'needs_ratings'
         END AS state,
         count(*) AS notes,
         count(*) FILTER (WHERE moderation = 'pending_review') AS pending
  FROM (SELECT *, moderation = 'visible' AS visible FROM note) note
  GROUP BY narrative_id
),
members AS (
  SELECT v.narrative_id, count(DISTINCT f.id) AS members, count(DISTINCT v.id) AS variants,
         min(f.first_seen) AS first_seen
  FROM variant v JOIN fingerprint f ON f.variant_id = v.id
  GROUP BY v.narrative_id
),
spread AS (
  SELECT v.narrative_id, count(DISTINCT o.page_id) AS pages, count(DISTINCT p.domain_id) AS sites,
         count(*) FILTER (WHERE o.source = 'detection') AS detections, max(o.seen_at) AS last_seen
  FROM variant v
  JOIN fingerprint f ON f.variant_id = v.id
  JOIN observation o ON o.fingerprint_id = f.id
  JOIN page p ON p.id = o.page_id
  GROUP BY v.narrative_id
)
SELECT n.id, n.title, n.claim, n.source, n.source_url, n.created_at,
       starts_with(coalesce(n.source_url, ''), '{DEMO_SEED}') AS demo,
       coalesce(nt.state, 'tracked') AS state,
       coalesce(nt.notes, 0) AS notes,
       coalesce(nt.pending, 0) AS pending,
       coalesce(m.members, 0) AS members,
       coalesce(m.variants, 0) AS variants,
       coalesce(s.pages, 0) AS pages,
       coalesce(s.sites, 0) AS sites,
       coalesce(s.detections, 0) AS detections,
       coalesce(m.first_seen, n.created_at) AS first_seen,
       s.last_seen
FROM narrative n
LEFT JOIN notes nt ON nt.narrative_id = n.id
LEFT JOIN members m ON m.narrative_id = n.id
LEFT JOIN spread s ON s.narrative_id = n.id
"""


def narratives(
    conn: Connection, q: str = "", state: str = "", source: str = "note", sort: str = "last_seen"
) -> list[dict]:
    where, params = ["true"], {"limit": MAX_ROWS}
    if source in ("note", "cluster"):
        where.append("n.source = %(source)s")
        params["source"] = source
    if state in STATES:
        where.append("coalesce(nt.state, 'tracked') = %(state)s")
        params["state"] = state
    if q.strip():
        where.append("(n.title ILIKE %(q)s OR n.claim ILIKE %(q)s OR n.id::text = %(id)s)")
        params["q"] = f"%{q.strip()}%"
        params["id"] = q.strip().lstrip("#")
    order = SORTS.get(sort, SORTS["last_seen"])
    sql = f"{NARRATIVES} WHERE {' AND '.join(where)} ORDER BY {order}, n.id DESC LIMIT %(limit)s"
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()


def narrative(conn: Connection, narrative_id: int) -> dict | None:
    return (
        conn.cursor(row_factory=dict_row)
        .execute(f"{NARRATIVES} WHERE n.id = %(id)s", {"id": narrative_id})
        .fetchone()
    )


# Every note with its author and rating counts. A rater is an account, or an install without one, as
# in app/jobs/score.py: each counts once per note with its latest rating, so `ratings` exceeds `counted`
# by the earlier ratings of an account's other installs. An install without an account writes notes
# with no account_id; its author is shown by a short install id.
NOTES = f"""
SELECT nt.id, nt.narrative_id, n.title AS narrative, n.claim AS passage, n.source_url AS url,
       starts_with(coalesce(n.source_url, ''), '{DEMO_SEED}') AS demo,
       nt.classification, nt.status, nt.decided_by, nt.moderation, nt.moderation_reason, nt.body,
       nt.sources, nt.intercept, nt.factor, nt.decided_at, nt.created_at,
       coalesce(a.email, 'Anonymous install ' || left(nt.author_install::text, 8)) AS author,
       nt.account_id IS NULL AS anonymous,
       (SELECT count(*) FROM note_rating r WHERE r.note_id = nt.id) AS ratings,
       rc.counted, rc.helpful, rc.somewhat, rc.not_helpful
FROM note nt
JOIN narrative n ON n.id = nt.narrative_id
LEFT JOIN user_account a ON a.id = nt.account_id
CROSS JOIN LATERAL (
  SELECT count(*) AS counted,
         count(*) FILTER (WHERE helpfulness = 'helpful') AS helpful,
         count(*) FILTER (WHERE helpfulness = 'somewhat') AS somewhat,
         count(*) FILTER (WHERE helpfulness = 'not_helpful') AS not_helpful
  FROM (
    SELECT DISTINCT ON (coalesce(i.account_id, r.install)) r.helpfulness
    FROM note_rating r JOIN install i ON i.id = r.install
    WHERE r.note_id = nt.id
    ORDER BY coalesce(i.account_id, r.install), r.created_at DESC
  ) latest
) rc
"""
NOTE_STATUSES = ("needs_more_ratings", "helpful", "not_helpful", "controversial")
MODERATIONS = ("visible", "pending_review", "hidden")
RULES = ("bridging", "agreement")


def notes(
    conn: Connection,
    status: str = "",
    moderation: str = "",
    narrative_id: int | None = None,
    note_id: int | None = None,
    rule: str = "",
) -> list[dict]:
    where, params = ["true"], {"limit": MAX_ROWS}
    if status in NOTE_STATUSES:
        where.append("nt.status = %(status)s")
        params["status"] = status
    if moderation in MODERATIONS:
        where.append("nt.moderation = %(moderation)s")
        params["moderation"] = moderation
    if rule in RULES:
        where.append("nt.decided_by = %(rule)s")
        params["rule"] = rule
    if narrative_id is not None:
        where.append("nt.narrative_id = %(narrative)s")
        params["narrative"] = narrative_id
    if note_id is not None:
        where.append("nt.id = %(note)s")
        params["note"] = note_id
    order = "nt.created_at" if moderation == "pending_review" else "nt.created_at DESC"
    sql = f"{NOTES} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT %(limit)s"
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()


def pending_count(conn: Connection) -> int:
    return conn.execute("SELECT count(*) FROM note WHERE moderation = 'pending_review'").fetchone()[0]


# How a member block came to carry its narrative.
Level = Literal["passage", "exact", "edited", "embedding"]
LEVELS: tuple[Level, ...] = ("passage", "exact", "edited", "embedding")


@dataclass
class Member:
    id: int
    content: str
    url: str | None
    domain: str | None
    first_seen: datetime
    pages: int
    hashes: set[str]
    minhash: list[int] | None
    dateless_hash: str | None
    similarity: float | None  # best cosine to the narrative's vectors, or to its centroid without any
    level: Level = "embedding"
    via: int | None = None  # the member it matched at the exact or edited level
    shared: int = 0  # sentences shared with `via`
    jaccard: float | None = None  # estimated Jaccard with `via`
    match: str = field(default="")  # 'narrative' or 'related' for embedding members past a threshold


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def group_members(
    claim: str | None, members: list[Member], derived: dict[tuple[int, int], float | None]
) -> list[Member]:
    """Label each member with how it joined the narrative, closest first.

    The passage is the noted text (the narrative's claim, or a block stored without a page). Exact
    members share a sentence with the passage or another exact member; edited ones are near-duplicates
    (estimated Jaccard of at least MIN_JACCARD, the same dateless text, or a DERIVED_FROM edge) of a
    passage, exact or edited member; the rest joined by embedding similarity. `derived` maps each
    DERIVED_FROM pair (child, parent) to its Jaccard.
    """
    by_id = {m.id: m for m in members}
    seeds = [m for m in members if (claim and _same_text(m.content, claim)) or m.url is None]
    for m in seeds:
        m.level = "passage"
    reached = {m.id for m in seeds}

    queue = deque(seeds)
    while queue:
        anchor = queue.popleft()
        if anchor.level not in ("passage", "exact"):
            continue
        for m in members:
            if m.id not in reached and m.hashes & anchor.hashes:
                m.level, m.via, m.shared = "exact", anchor.id, len(m.hashes & anchor.hashes)
                reached.add(m.id)
                queue.append(m)

    signed = [m for m in members if m.minhash]
    signatures = np.array([m.minhash for m in signed]) if signed else np.zeros((0, 0))
    neighbours: dict[int, set[int]] = {m.id: set() for m in members}
    for child, parent in derived:
        if child in by_id and parent in by_id:
            neighbours[child].add(parent)
            neighbours[parent].add(child)
    queue = deque(by_id[i] for i in sorted(reached))
    while queue:
        anchor = queue.popleft()
        estimates: dict[int, float] = {}
        if anchor.minhash and len(signed):
            shares = (signatures == np.array(anchor.minhash)).mean(axis=1)
            estimates = {m.id: float(s) for m, s in zip(signed, shares, strict=True)}
        for m in members:
            if m.id in reached:
                continue
            jaccard = estimates.get(m.id)
            edge = (m.id, anchor.id) in derived or (anchor.id, m.id) in derived
            same = m.dateless_hash is not None and m.dateless_hash == anchor.dateless_hash
            if edge or same or (jaccard is not None and jaccard >= MIN_JACCARD):
                stored = derived.get((m.id, anchor.id)) or derived.get((anchor.id, m.id))
                m.level, m.via, m.jaccard = "edited", anchor.id, stored if stored is not None else jaccard
                reached.add(m.id)
                queue.append(m)

    for m in members:
        if m.level == "embedding" and m.similarity is not None:
            if m.similarity >= settings.narrative_match_threshold:
                m.match = "narrative"
            elif m.similarity >= settings.related_match_threshold:
                m.match = "related"
    order = {level: i for i, level in enumerate(LEVELS)}
    return sorted(
        members,
        key=lambda m: (order[m.level], -(m.jaccard or 0), -(m.similarity or 0), m.first_seen, m.id),
    )


def members(conn: Connection, narrative_id: int, claim: str | None) -> list[Member]:
    rows = conn.execute(
        """
        SELECT f.id, f.content, p.url, d.name, f.first_seen,
               (SELECT count(DISTINCT o.page_id) FROM observation o WHERE o.fingerprint_id = f.id),
               coalesce((SELECT array_agg(s.hash) FROM sentence s WHERE s.fingerprint_id = f.id), '{}'),
               f.minhash, f.dateless_hash,
               coalesce(
                 (SELECT max(1 - (nv.embedding <=> f.embedding)) FROM narrative_vector nv
                  WHERE nv.narrative_id = v.narrative_id),
                 1 - (f.embedding <=> n.centroid))
        FROM fingerprint f
        JOIN variant v ON v.id = f.variant_id
        JOIN narrative n ON n.id = v.narrative_id
        LEFT JOIN page p ON p.id = f.first_page_id
        LEFT JOIN domain d ON d.id = p.domain_id
        WHERE v.narrative_id = %s
        ORDER BY f.first_seen, f.id
        LIMIT %s
        """,
        (narrative_id, MAX_MEMBERS),
    ).fetchall()
    found = [
        Member(id_, content, url, domain, seen, pages, set(hashes), minhash, dateless, similarity)
        for id_, content, url, domain, seen, pages, hashes, minhash, dateless, similarity in rows
    ]
    return group_members(claim, found, derived_edges(conn, [m.id for m in found]))


def derived_edges(conn: Connection, fingerprint_ids: list[int]) -> dict[tuple[int, int], float | None]:
    """DERIVED_FROM edges among the fingerprints, as (child, parent) to the stored Jaccard."""
    rows = conn.execute(
        "SELECT src, dst, (props->>'jaccard')::float FROM edge"
        " WHERE type = 'DERIVED_FROM' AND src_kind = 'fingerprint' AND dst_kind = 'fingerprint'"
        " AND src = ANY(%(ids)s) AND dst = ANY(%(ids)s)",
        {"ids": fingerprint_ids},
    ).fetchall()
    return {(src, dst): jaccard for src, dst, jaccard in rows}


def sightings(conn: Connection, fingerprint_ids: list[int]) -> list[tuple[datetime, str, int]]:
    """Observations of the fingerprints per day and source (fetch or detection)."""
    return conn.execute(
        "SELECT time_bucket('1 day', seen_at), source, count(*) FROM observation"
        " WHERE fingerprint_id = ANY(%s) GROUP BY 1, 2 ORDER BY 1",
        (fingerprint_ids,),
    ).fetchall()


def pages(conn: Connection, fingerprint_ids: list[int]) -> list[dict]:
    return (
        conn.cursor(row_factory=dict_row)
        .execute(
            """
            SELECT p.url, d.name AS domain, count(*) AS observations,
                   count(*) FILTER (WHERE o.source = 'detection') AS detections,
                   min(o.seen_at) AS first_seen, max(o.seen_at) AS last_seen
            FROM observation o JOIN page p ON p.id = o.page_id JOIN domain d ON d.id = p.domain_id
            WHERE o.fingerprint_id = ANY(%s)
            GROUP BY p.id, d.name
            ORDER BY first_seen
            LIMIT 100
            """,
            (fingerprint_ids,),
        )
        .fetchall()
    )


def evolution(conn: Connection, narrative_id: int) -> list[dict]:
    """EVOLVED_INTO edges into and out of the narrative."""
    return (
        conn.cursor(row_factory=dict_row)
        .execute(
            """
            SELECT e.src = %(id)s AS outgoing, n.id, n.title, n.source, (e.props->>'similarity')::float AS similarity
            FROM edge e JOIN narrative n ON n.id = CASE WHEN e.src = %(id)s THEN e.dst ELSE e.src END
            WHERE e.type = 'EVOLVED_INTO' AND e.src_kind = 'narrative' AND (e.src = %(id)s OR e.dst = %(id)s)
            """,
            {"id": narrative_id},
        )
        .fetchall()
    )


# At most this many member blocks are projected on the cluster map: noted narratives' first, then the newest.
MAP_POINTS = 4000


def map_points(conn: Connection) -> tuple[list[dict], list[dict]]:
    """Member blocks and narrative claim vectors projected to 2D by PCA over the embeddings.

    Returns the points, each {x, y, kind 'block' | 'claim', narrative_id, fingerprint_id, text}, and the
    narratives they belong to.
    """
    blocks = conn.execute(
        """
        SELECT f.id, v.narrative_id, left(f.content, 160), f.embedding::real[]
        FROM fingerprint f JOIN variant v ON v.id = f.variant_id JOIN narrative n ON n.id = v.narrative_id
        WHERE f.embedding IS NOT NULL
        ORDER BY n.source = 'note' DESC, f.first_seen DESC
        LIMIT %s
        """,
        (MAP_POINTS,),
    ).fetchall()
    claims = conn.execute(
        "SELECT NULL, narrative_id, NULL, embedding::real[] FROM narrative_vector WHERE kind = 'claim'"
    ).fetchall()
    rows = [("block", *row) for row in blocks] + [("claim", *row) for row in claims]
    if len(rows) < 2:
        return [], []
    xy = project([row[4] for row in rows])
    points = [
        {
            "x": float(x),
            "y": float(y),
            "kind": kind,
            "fingerprint_id": fid,
            "narrative_id": nid,
            "text": text,
        }
        for (kind, fid, nid, text, _), (x, y) in zip(rows, xy, strict=True)
    ]
    ids = sorted({p["narrative_id"] for p in points})
    listed = conn.cursor(row_factory=dict_row).execute(
        f"{NARRATIVES} WHERE n.id = ANY(%(ids)s) ORDER BY members DESC, n.id", {"ids": ids}
    )
    return points, listed.fetchall()


def project(embeddings: list[list[float]]) -> np.ndarray:
    """The first two principal components of the embeddings, scaled into [0, 1] with the aspect kept."""
    x = np.asarray(embeddings, dtype=np.float64)
    x = x - x.mean(axis=0)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    xy = x @ vt[:2].T
    xy = xy - xy.min(axis=0)
    span = xy.max()
    return xy / span if span > 0 else xy


def cluster_candidates(conn: Connection) -> list[dict]:
    """Narratives the cluster job found among blocks without a note, largest first, with what they
    evolved from."""
    return (
        conn.cursor(row_factory=dict_row)
        .execute(
            f"""
            SELECT c.*, e.src AS evolved_from, src.title AS evolved_from_title
            FROM ({NARRATIVES} WHERE n.source = 'cluster') c
            LEFT JOIN edge e ON e.type = 'EVOLVED_INTO' AND e.dst_kind = 'narrative' AND e.dst = c.id
            LEFT JOIN narrative src ON src.id = e.src
            ORDER BY c.members DESC, c.id DESC
            LIMIT %s
            """,
            (MAX_ROWS,),
        )
        .fetchall()
    )


def last_cluster_run(conn: Connection) -> dict | None:
    return (
        conn.cursor(row_factory=dict_row)
        .execute("SELECT * FROM cluster_run ORDER BY ran_at DESC LIMIT 1")
        .fetchone()
    )


def users(conn: Connection) -> list[dict]:
    """Accounts and installs without one that wrote or rated notes, with their standing, most active
    first. An install without an account has the capped reputation of trust.py."""
    rows = conn.execute(
        """
        SELECT a.id, a.email, a.created_at,
               (SELECT count(*) FROM install i WHERE i.account_id = a.id) AS installs,
               (SELECT count(*) FROM note n WHERE n.account_id = a.id) AS notes
        FROM user_account a
        ORDER BY notes DESC, a.created_at DESC
        LIMIT %s
        """,
        (MAX_ROWS,),
    ).fetchall()
    result = []
    for account_id, email, created, installs, note_count in rows:
        standing = trust.account_standing(conn, account_id, email)
        result.append(
            {
                "id": account_id,
                "name": email,
                "anonymous": False,
                "created_at": created,
                "installs": installs,
                "notes": note_count,
                "helpful_notes": standing.helpful_notes,
                "unhelpful_notes": standing.unhelpful_notes,
                "ratings": standing.ratings,
                "agreed": standing.agreed,
                "disagreed": standing.disagreed,
                "reputation": standing.reputation,
                "moderator": standing.moderator,
                "listed": email in settings.moderator_emails,
            }
        )
    anonymous = (
        conn.cursor(row_factory=dict_row)
        .execute(
            """
            SELECT i.id, i.created_at,
                   count(nt.id) AS notes,
                   count(nt.id) FILTER (WHERE nt.status = 'helpful') AS helpful_notes,
                   count(nt.id) FILTER (WHERE nt.status = 'not_helpful') AS unhelpful_notes,
                   (SELECT count(*) FROM note_rating r WHERE r.install = i.id) AS ratings
            FROM install i
            LEFT JOIN note nt ON nt.author_install = i.id AND nt.account_id IS NULL
            WHERE i.account_id IS NULL
              AND (EXISTS (SELECT 1 FROM note_rating r WHERE r.install = i.id)
                   OR EXISTS (SELECT 1 FROM note n WHERE n.author_install = i.id AND n.account_id IS NULL))
            GROUP BY i.id
            LIMIT %s
            """,
            (MAX_ROWS,),
        )
        .fetchall()
    )
    capped = trust.Standing(None, None).reputation
    for row in anonymous:
        result.append(
            {
                **row,
                "name": f"Anonymous install {str(row['id'])[:8]}",
                "anonymous": True,
                "installs": None,
                "agreed": None,
                "disagreed": None,
                "reputation": capped,
                "moderator": False,
                "listed": False,
            }
        )
    result.sort(key=lambda u: (-(u["notes"] + u["ratings"]), u["anonymous"], u["name"]))
    return result
