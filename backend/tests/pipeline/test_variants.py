from datetime import UTC, datetime, timedelta

import numpy as np

from app import fpclient
from app.jobs.variants import MAX_BLOCK_VECTORS, handle, refresh_narrative_vectors

ORIGINAL = (
    "Western sanctions against Russia have backfired and are destroying the European economy, while ordinary "
    "citizens in Germany and France pay record prices for gas and electricity because their governments "
    "follow orders from Washington instead of defending national interests."
)
CLOSE_COPY = ORIGINAL.replace("record prices", "insane prices")
FAR_COPY = CLOSE_COPY.replace("Washington", "the White House").replace("ordinary", "hard-working")
UNRELATED = (
    "The city council approved a new bicycle lane along the river after a long public consultation in which "
    "residents asked for safer routes to schools, parks and the central railway station."
)
T0 = datetime(2026, 11, 1, tzinfo=UTC)


def add_fingerprint(conn, text, first_seen, variant_id=None, embed=False):
    block = fpclient.fingerprint([text], embed=embed)["blocks"][0]
    fingerprint_id = conn.execute(
        "INSERT INTO fingerprint (content_hash, content, minhash, embedding, variant_id, first_seen)"
        " VALUES (%s, %s, %s, %s::vector, %s, %s) RETURNING id",
        (
            block["contentHash"],
            text,
            block["minhash"],
            block["embedding"] and str(block["embedding"]),
            variant_id,
            first_seen,
        ),
    ).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO lsh_band (band, hash, fingerprint_id) VALUES (%s, %s, %s)",
            [(i, h, fingerprint_id) for i, h in enumerate(block["bands"])],
        )
    return fingerprint_id


def add_variant(conn):
    narrative_id = conn.execute(
        "INSERT INTO narrative (title, source) VALUES ('Sanctions backfire', 'euvsdisinfo') RETURNING id"
    ).fetchone()[0]
    return narrative_id, conn.execute(
        "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative_id,)
    ).fetchone()[0]


def variant_of(conn, fingerprint_id):
    return conn.execute("SELECT variant_id FROM fingerprint WHERE id = %s", (fingerprint_id,)).fetchone()[0]


def edges(conn):
    return conn.execute("SELECT src, dst, type, props FROM edge").fetchall()


def test_an_edited_copy_joins_the_originals_variant_and_derives_from_it(conn):
    _, variant_id = add_variant(conn)
    original = add_fingerprint(conn, ORIGINAL, T0, variant_id)
    copy = add_fingerprint(conn, CLOSE_COPY, T0 + timedelta(hours=2))

    handle(conn, {"fingerprint_ids": [copy]})

    assert variant_of(conn, copy) == variant_id
    [(src, dst, type_, props)] = edges(conn)
    assert (src, dst, type_, props["dt_seconds"]) == (copy, original, "DERIVED_FROM", 7200)
    assert 0.7 < props["jaccard"] < 1


def test_unrelated_text_joins_nothing(conn):
    _, variant_id = add_variant(conn)
    add_fingerprint(conn, ORIGINAL, T0, variant_id)
    other = add_fingerprint(conn, UNRELATED, T0 + timedelta(hours=1))

    handle(conn, {"fingerprint_ids": [other]})

    assert variant_of(conn, other) is None
    assert edges(conn) == []


def test_a_fingerprint_with_a_variant_keeps_it_and_still_derives(conn):
    _, original_variant = add_variant(conn)
    _, own_variant = add_variant(conn)
    original = add_fingerprint(conn, ORIGINAL, T0, original_variant)
    copy = add_fingerprint(conn, CLOSE_COPY, T0 + timedelta(hours=1), own_variant)

    handle(conn, {"fingerprint_ids": [copy]})

    assert variant_of(conn, copy) == own_variant
    assert [(src, dst) for src, dst, _, _ in edges(conn)] == [(copy, original)]


def test_lineage_points_to_the_most_similar_earlier_copy(conn):
    add_fingerprint(conn, FAR_COPY, T0)
    close = add_fingerprint(conn, CLOSE_COPY, T0 + timedelta(hours=1))
    add_fingerprint(conn, ORIGINAL, T0 + timedelta(hours=3))
    newest = add_fingerprint(
        conn, ORIGINAL + " Share this article with everyone you know.", T0 + timedelta(hours=2)
    )

    handle(conn, {"fingerprint_ids": [newest]})

    assert [(src, dst) for src, dst, _, _ in edges(conn)] == [(newest, close)]


def test_joining_a_narrative_updates_its_centroid(conn):
    narrative_id, variant_id = add_variant(conn)
    add_fingerprint(conn, ORIGINAL, T0, variant_id, embed=True)
    copy = add_fingerprint(conn, CLOSE_COPY, T0 + timedelta(hours=1), embed=True)

    handle(conn, {"fingerprint_ids": [copy]})

    embeddings = [row[0] for row in conn.execute("SELECT embedding::real[] FROM fingerprint")]
    centroid = conn.execute(
        "SELECT centroid::real[] FROM narrative WHERE id = %s", (narrative_id,)
    ).fetchone()[0]
    assert np.allclose(centroid, np.mean(embeddings, axis=0), atol=1e-6)


def test_boilerplate_joins_no_variant_but_still_derives(conn):
    _, variant_id = add_variant(conn)
    original = add_fingerprint(conn, ORIGINAL, T0, variant_id)
    copy = add_fingerprint(conn, CLOSE_COPY, T0 + timedelta(hours=1))
    domain_id = conn.execute("INSERT INTO domain (name) VALUES ('example.org') RETURNING id").fetchone()[0]
    for path in ("a", "b"):
        page_id = conn.execute(
            "INSERT INTO page (url, domain_id, status) VALUES (%s, %s, 'ok') RETURNING id",
            (f"https://example.org/{path}", domain_id),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO observation (seen_at, fingerprint_id, page_id, source) VALUES (%s, %s, %s, 'fetch')",
            (T0, copy, page_id),
        )

    handle(conn, {"fingerprint_ids": [copy]})

    assert variant_of(conn, copy) is None
    assert [(src, dst) for src, dst, *_ in edges(conn)] == [(copy, original)]


SANCTIONS = [
    ORIGINAL,
    CLOSE_COPY,
    "Sanctions on Russian energy hurt Europe far more than Russia, and European industry is collapsing as a result.",
    "European households cannot afford heating this winter because Brussels imposed sanctions that backfired on Europe.",
    "The sanctions policy dictated by Washington is ruining German factories and pushing France into recession.",
    "Russia sells its gas elsewhere while Europeans pay the bill for sanctions their leaders were ordered to adopt.",
]


def vectors(conn, narrative_id):
    return conn.execute(
        "SELECT kind, fingerprint_id FROM narrative_vector WHERE narrative_id = %s", (narrative_id,)
    ).fetchall()


def test_narrative_vectors_hold_the_central_blocks_and_the_claim(conn):
    narrative_id, variant_id = add_variant(conn)
    conn.execute(
        "UPDATE narrative SET claim = 'Sanctions against Russia destroy the European economy.' WHERE id = %s",
        (narrative_id,),
    )
    related = [add_fingerprint(conn, text, T0, variant_id, embed=True) for text in SANCTIONS]
    off_topic = add_fingerprint(conn, UNRELATED, T0, variant_id, embed=True)
    unfetched = conn.execute(
        "INSERT INTO narrative (title, claim, source) VALUES"
        " ('Vaccines', 'Vaccines alter human DNA and the authorities hide it from the public.', 'euvsdisinfo')"
        " RETURNING id"
    ).fetchone()[0]

    refresh_narrative_vectors(conn, [narrative_id, unfetched])

    kept = vectors(conn, narrative_id)
    assert ("claim", None) in kept
    blocks = {fingerprint_id for kind, fingerprint_id in kept if kind == "block"}
    assert len(blocks) == MAX_BLOCK_VECTORS and blocks < set(related) and off_topic not in blocks
    assert vectors(conn, unfetched) == [("claim", None)]

    # Unchanged vectors are not rewritten, so the index sends no delta for them.
    conn.commit()
    changes = conn.execute("SELECT count(*) FROM index_change").fetchone()[0]
    refresh_narrative_vectors(conn, [narrative_id, unfetched])
    assert conn.execute("SELECT count(*) FROM index_change").fetchone()[0] == changes
