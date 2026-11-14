import numpy as np
import pytest

from app.jobs import cluster
from app.jobs.cluster import handle

DIM = 384


def direction(i):
    v = np.zeros(DIM)
    v[i] = 1.0
    return v


def blend(a, b, similarity):
    """A unit vector whose cosine similarity to unit vector a is `similarity`, tilted towards b."""
    return similarity * a + np.sqrt(1 - similarity**2) * b


def group(center, first_axis, size=4, spread=0.05):
    """`size` points around `center`: the center itself, then small offsets along unused axes."""
    return [center] + [center + spread * direction(first_axis + k) for k in range(size - 1)]


def add_fingerprint(conn, embedding, content="Text. More.", variant_id=None):
    return conn.execute(
        "INSERT INTO fingerprint (content_hash, content, embedding, variant_id)"
        " VALUES (gen_random_uuid()::text, %s, %s::real[]::vector, %s) RETURNING id",
        (content, list(map(float, embedding)), variant_id),
    ).fetchone()[0]


def add_group(conn, points, title):
    ids = [add_fingerprint(conn, p, f"Member {i} of {title}. Rest.") for i, p in enumerate(points[1:])]
    return [add_fingerprint(conn, points[0], f"{title}. Second sentence.")] + ids


def add_narrative(conn, embeddings):
    narrative_id = conn.execute(
        "INSERT INTO narrative (title, source, state) VALUES ('Known', 'euvsdisinfo', 'active') RETURNING id"
    ).fetchone()[0]
    variant_id = conn.execute(
        "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative_id,)
    ).fetchone()[0]
    for e in embeddings:
        add_fingerprint(conn, e, variant_id=variant_id)
    return narrative_id


def narrative_of(conn, fingerprint_ids):
    return {
        row[0]
        for row in conn.execute(
            "SELECT v.narrative_id FROM fingerprint f LEFT JOIN variant v ON v.id = f.variant_id"
            " WHERE f.id = ANY(%s)",
            (fingerprint_ids,),
        )
    }


def test_new_clusters_become_candidate_narratives_titled_by_their_central_member(conn):
    a = add_group(conn, group(direction(0), 100), "Alpha claim")
    b = add_group(conn, group(direction(1), 110), "Beta claim")

    handle(conn, {})

    rows = conn.execute("SELECT id, title, source, state FROM narrative ORDER BY title").fetchall()
    assert [r[1:] for r in rows] == [
        ("Alpha claim.", "cluster", "candidate"),
        ("Beta claim.", "cluster", "candidate"),
    ]
    assert narrative_of(conn, a) == {rows[0][0]}
    assert narrative_of(conn, b) == {rows[1][0]}


def test_cluster_near_a_narrative_joins_it_as_a_new_variant(conn):
    known = add_narrative(conn, [direction(0), direction(0) + 0.05 * direction(200)])
    near = add_group(conn, group(direction(0), 100), "Alpha again")
    add_group(conn, group(direction(1), 110), "Beta claim")

    handle(conn, {})

    assert narrative_of(conn, near) == {known}
    assert conn.execute("SELECT count(*) FROM variant WHERE narrative_id = %s", (known,)).fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM narrative WHERE source = 'cluster'").fetchone()[0] == 1


def test_cluster_resembling_an_earlier_one_evolves_from_it(conn):
    add_group(conn, group(direction(0), 100), "Alpha claim")
    add_group(conn, group(direction(1), 110), "Beta claim")
    handle(conn, {})
    alpha = conn.execute("SELECT id FROM narrative WHERE title = 'Alpha claim.'").fetchone()[0]

    similarity = (cluster.EVOLVE_SIMILARITY + cluster.EXTEND_SIMILARITY) / 2
    add_group(conn, group(blend(direction(0), direction(2), similarity), 120), "Alpha evolved")
    add_group(conn, group(direction(3), 130), "Gamma claim")
    handle(conn, {})

    evolved = conn.execute("SELECT id FROM narrative WHERE title = 'Alpha evolved.'").fetchone()[0]
    edges = conn.execute(
        "SELECT src_kind, src, dst_kind, dst, props->>'similarity' FROM edge WHERE type = 'EVOLVED_INTO'"
    ).fetchall()
    assert [e[:4] for e in edges] == [("narrative", alpha, "narrative", evolved)]
    assert float(edges[0][4]) == pytest.approx(similarity, abs=0.02)


def test_run_clusters_at_most_the_cap_and_records_it(conn, monkeypatch):
    monkeypatch.setattr(cluster, "MAX_EMBEDDINGS", 8)
    oldest = add_group(conn, group(direction(5), 140, size=2), "Old")
    conn.execute("UPDATE fingerprint SET first_seen = now() - interval '1 day' WHERE id = ANY(%s)", (oldest,))
    add_group(conn, group(direction(0), 100), "Alpha claim")
    add_group(conn, group(direction(1), 110), "Beta claim")

    handle(conn, {})

    assert conn.execute("SELECT unassigned, embeddings, clusters FROM cluster_run").fetchall() == [(10, 8, 2)]
    assert narrative_of(conn, oldest) == {None}


def test_first_sentence_ends_at_the_first_sentence_break():
    assert cluster.first_sentence("  NATO is at the border! Everyone knows it.") == "NATO is at the border!"
