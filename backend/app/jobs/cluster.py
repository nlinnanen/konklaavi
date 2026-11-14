"""Periodic narrative discovery: HDBSCAN over the embeddings of fingerprints that have no variant."""

import logging
import re
from dataclasses import dataclass

import numpy as np
from psycopg import Connection
from psycopg.types.json import Jsonb
from sklearn.cluster import HDBSCAN

from app.jobs import register

# Bounds the run's cost; the newest unassigned embeddings are clustered first.
MAX_EMBEDDINGS = 2000
MIN_CLUSTER_SIZE = 3
# Cosine similarity of a cluster's centroid to a narrative's centroid at which the cluster joins the narrative.
EXTEND_SIMILARITY = 0.9
# From here up to EXTEND_SIMILARITY, the cluster becomes a new narrative the nearest one evolved into.
EVOLVE_SIMILARITY = 0.75
TITLE_CHARS = 200

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cluster:
    fingerprint_ids: list[int]
    centroid: list[float]  # unit length
    central_id: int  # the member closest to the centroid


def _unit(x: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.where(norms == 0, 1, norms)


def find_clusters(ids: list[int], embeddings: np.ndarray) -> list[Cluster]:
    """HDBSCAN clusters of the embeddings by cosine distance; noise points belong to none."""
    if len(ids) < MIN_CLUSTER_SIZE:
        return []
    # Euclidean distance between unit vectors orders pairs as cosine distance does.
    points = _unit(np.asarray(embeddings, dtype=np.float64))
    labels = HDBSCAN(min_cluster_size=MIN_CLUSTER_SIZE, copy=False).fit_predict(points)
    clusters = []
    for label in sorted(set(labels.tolist()) - {-1}):
        members = np.flatnonzero(labels == label)
        centroid = _unit(points[members].mean(axis=0))
        central = members[int(np.argmax(points[members] @ centroid))]
        clusters.append(Cluster([ids[i] for i in members], centroid.tolist(), ids[central]))
    return clusters


def first_sentence(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    return sentence[:TITLE_CHARS]


def refresh_centroids(conn: Connection, narrative_ids: list[int] | None = None) -> None:
    """Set each narrative's centroid to the mean embedding of its fingerprints, or of the given ones only."""
    conn.execute(
        """
        UPDATE narrative n SET centroid = c.centroid
        FROM (
          SELECT v.narrative_id, avg(f.embedding) AS centroid
          FROM fingerprint f JOIN variant v ON v.id = f.variant_id
          WHERE f.embedding IS NOT NULL AND v.narrative_id IS NOT NULL
            AND (%(ids)s::bigint[] IS NULL OR v.narrative_id = ANY(%(ids)s::bigint[]))
          GROUP BY v.narrative_id
        ) c
        WHERE n.id = c.narrative_id
        """,
        {"ids": narrative_ids},
    )


def nearest_narrative(
    conn: Connection, centroid: list[float], exclude: list[int]
) -> tuple[int, float] | None:
    """The narrative whose centroid is most cosine-similar to the given one, and that similarity."""
    return conn.execute(
        """
        SELECT id, 1 - (centroid <=> %(c)s::real[]::vector)
        FROM narrative
        WHERE centroid IS NOT NULL AND id <> ALL(%(exclude)s::bigint[])
        ORDER BY centroid <=> %(c)s::real[]::vector
        LIMIT 1
        """,
        {"c": centroid, "exclude": exclude},
    ).fetchone()


def add_variant(conn: Connection, narrative_id: int, fingerprint_ids: list[int]) -> None:
    variant_id = conn.execute(
        "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative_id,)
    ).fetchone()[0]
    conn.execute("UPDATE fingerprint SET variant_id = %s WHERE id = ANY(%s)", (variant_id, fingerprint_ids))


def create_narrative(conn: Connection, cluster: Cluster, title: str) -> int:
    return conn.execute(
        "INSERT INTO narrative (title, source, state, centroid)"
        " VALUES (%s, 'cluster', 'candidate', %s::real[]::vector) RETURNING id",
        (title, cluster.centroid),
    ).fetchone()[0]


# Unassigned embedded fingerprints, leaving out boilerplate: text seen on 2+ pages of one domain.
CLUSTERABLE = """
    variant_id IS NULL AND embedding IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM observation o JOIN page p ON p.id = o.page_id
      WHERE o.fingerprint_id = fingerprint.id
      GROUP BY p.domain_id HAVING count(DISTINCT o.page_id) >= 2
    )
"""


@register("cluster")
def handle(conn: Connection, payload: dict) -> None:
    refresh_centroids(conn)
    unassigned = conn.execute(f"SELECT count(*) FROM fingerprint WHERE {CLUSTERABLE}").fetchone()[0]
    rows = conn.execute(
        f"SELECT id, content, embedding::real[] FROM fingerprint WHERE {CLUSTERABLE}"
        " ORDER BY first_seen DESC, id DESC LIMIT %s",
        (MAX_EMBEDDINGS,),
    ).fetchall()
    content = {row[0]: row[1] for row in rows}
    clusters = find_clusters([row[0] for row in rows], np.array([row[2] for row in rows])) if rows else []

    created: list[int] = []
    extended: list[int] = []
    evolved = 0
    for cluster in clusters:
        # Narratives created in this run are left out, so a run's clusters never join or evolve from each other.
        nearest = nearest_narrative(conn, cluster.centroid, created)
        if nearest is not None and nearest[1] >= EXTEND_SIMILARITY:
            add_variant(conn, nearest[0], cluster.fingerprint_ids)
            extended.append(nearest[0])
            continue
        narrative_id = create_narrative(conn, cluster, first_sentence(content[cluster.central_id]))
        add_variant(conn, narrative_id, cluster.fingerprint_ids)
        created.append(narrative_id)
        if nearest is not None and nearest[1] >= EVOLVE_SIMILARITY:
            conn.execute(
                "INSERT INTO edge (src_kind, src, dst_kind, dst, type, props)"
                " VALUES ('narrative', %s, 'narrative', %s, 'EVOLVED_INTO', %s) ON CONFLICT DO NOTHING",
                (nearest[0], narrative_id, Jsonb({"similarity": round(float(nearest[1]), 4)})),
            )
            evolved += 1
    if extended:
        refresh_centroids(conn, extended)

    conn.execute(
        "INSERT INTO cluster_run (unassigned, embeddings, clusters, extended, created, evolved)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (unassigned, len(rows), len(clusters), len(extended), len(created), evolved),
    )
    log.info(
        "clustered %d of %d unassigned embeddings: %d clusters, %d extended, %d new, %d evolved",
        len(rows),
        unassigned,
        len(clusters),
        len(extended),
        len(created),
        evolved,
    )
