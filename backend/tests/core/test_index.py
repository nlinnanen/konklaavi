import base64

import psycopg
import pytest

from app.config import settings

BANDS = [f"b{i:02}" for i in range(32)]
UNIT = [1.0] + [0.0] * 383


def add_bands(conn, content_hash, bands):
    """Store LSH bands for the fingerprint with this content hash; bands[i] is band i."""
    conn.execute(
        "INSERT INTO lsh_band (band, hash, fingerprint_id)"
        " SELECT b.band - 1, b.hash, f.id FROM fingerprint f, unnest(%s::text[]) WITH ORDINALITY b(hash, band)"
        " WHERE f.content_hash = %s",
        (bands, content_hash),
    )
    conn.commit()


def get_index(client, since=None):
    response = client.get("/index", params={} if since is None else {"since": since})
    assert response.status_code == 200, response.text
    return response.json()


def shipped(index):
    return [(n["narrativeId"], n["state"]) for n in index["narratives"]]


def set_moderation(conn, narrative_id, moderation):
    conn.execute("UPDATE note SET moderation = %s WHERE narrative_id = %s", (moderation, narrative_id))
    conn.commit()


def test_full_index_ships_shipped_narratives_with_sentences_and_bands_per_fingerprint(
    client, conn, narrative
):
    active = narrative(["s1", "s2"], content_hash="fp-1")
    add_bands(conn, "fp-1", BANDS)
    conn.execute(
        "INSERT INTO narrative_vector (narrative_id, kind, embedding) VALUES (%s, 'claim', %s::vector)",
        (active, str(UNIT)),
    )
    conn.commit()
    review = narrative(["s3"], moderation="pending_review", content_hash="fp-2")
    narrative(["s5"], moderation="hidden", content_hash="fp-4")
    conn.execute("INSERT INTO narrative (title, source, state) VALUES ('Candidate', 'cluster', 'candidate')")
    conn.commit()

    index = get_index(client)
    assert shipped(index) == [(str(active), "needs_ratings"), (str(review), "tracked")]
    assert {k: v for k, v in index.items() if k not in ("version", "narratives")} == {
        "full": True,
        "sentences": [
            {"hash": "s1", "narrativeId": str(active)},
            {"hash": "s2", "narrativeId": str(active)},
            {"hash": "s3", "narrativeId": str(review)},
        ],
        "fingerprints": [{"narrativeId": str(active), "bands": BANDS}],
        "narrativeThreshold": settings.narrative_match_threshold,
        "relatedThreshold": settings.related_match_threshold,
        "narrativeVectors": [
            {"narrativeId": str(active), "vectors": [base64.b64encode(bytes([127] + [0] * 383)).decode()]},
            {"narrativeId": str(review), "vectors": []},
        ],
        "removed": [],
    }


def test_delta_carries_changed_narratives_in_full_and_lists_those_no_longer_shipped(client, conn, narrative):
    extended = narrative(["s1"], content_hash="fp-1")
    hidden = narrative(["s2"], content_hash="fp-2")
    narrative(["s3"], content_hash="fp-3")
    version = get_index(client)["version"]

    conn.execute(
        "INSERT INTO sentence (hash, fingerprint_id) SELECT 's1b', id FROM fingerprint WHERE content_hash = 'fp-1'"
    )
    conn.commit()
    set_moderation(conn, hidden, "hidden")
    conn.execute("UPDATE narrative SET centroid = array_fill(0.1, ARRAY[384])::vector, updated_at = now()")
    conn.commit()

    delta = get_index(client, since=version)
    assert delta["version"] > version
    assert shipped(delta) == [(str(extended), "needs_ratings")]
    assert {k: v for k, v in delta.items() if k not in ("version", "narratives")} == {
        "full": False,
        "sentences": [
            {"hash": "s1", "narrativeId": str(extended)},
            {"hash": "s1b", "narrativeId": str(extended)},
        ],
        "fingerprints": [],
        "narrativeThreshold": settings.narrative_match_threshold,
        "relatedThreshold": settings.related_match_threshold,
        "narrativeVectors": [{"narrativeId": str(extended), "vectors": []}],
        "removed": [str(hidden)],
    }


def test_a_fingerprint_joining_a_narrative_changes_the_index(client, conn, narrative):
    target = narrative(["s1"], content_hash="fp-1")
    conn.execute("INSERT INTO fingerprint (content_hash, content) VALUES ('fp-new', 'copy')")
    conn.execute(
        "INSERT INTO sentence (hash, fingerprint_id) SELECT 's9', id FROM fingerprint WHERE content_hash = 'fp-new'"
    )
    conn.commit()
    version = get_index(client)["version"]

    conn.execute(
        "UPDATE fingerprint SET variant_id = (SELECT id FROM variant WHERE narrative_id = %s)"
        " WHERE content_hash = 'fp-new'",
        (target,),
    )
    conn.commit()

    delta = get_index(client, since=version)
    assert [n["narrativeId"] for n in delta["narratives"]] == [str(target)]
    assert {s["hash"] for s in delta["sentences"]} == {"s1", "s9"}


def test_a_note_decided_helpful_changes_the_index(client, conn, narrative):
    target = narrative(["s1"], content_hash="fp-1")
    narrative(["s2"], content_hash="fp-2")
    version = get_index(client)["version"]

    conn.execute("UPDATE note SET status = 'helpful' WHERE narrative_id = %s", (target,))
    conn.commit()
    assert shipped(get_index(client, since=version)) == [(str(target), "misleading")]


def test_a_version_newer_than_the_database_gets_the_full_index(client, narrative):
    narrative(["s1"])
    version = get_index(client)["version"]
    index = get_index(client, since=version + 1_000_000)
    assert (index["full"], len(index["narratives"])) == (True, 1)


def post_hits(client, *hits):
    body = {
        "hits": [
            {"hash": h, "narrativeId": str(n), "url": url, "seenAt": "2026-11-14T10:00:00Z"}
            for h, n, url in hits
        ]
    }
    response = client.post("/hits", json=body)
    assert response.status_code == 200, response.text


def test_a_hit_records_a_detection_and_fetches_the_page_once_a_day(client, conn, narrative):
    narrative_id = narrative(["s1"], content_hash="fp-1")
    post_hits(client, ("s1", narrative_id, "https://www.copy.example/a"))
    post_hits(client, ("fp-1", narrative_id, "https://www.copy.example/a"))

    observations = conn.execute(
        "SELECT f.content_hash, p.url, d.name, o.account_id, o.source FROM observation o"
        " JOIN fingerprint f ON f.id = o.fingerprint_id JOIN page p ON p.id = o.page_id"
        " JOIN domain d ON d.id = p.domain_id"
    ).fetchall()
    assert observations == [("fp-1", "https://www.copy.example/a", "copy.example", None, "detection")] * 2
    jobs = conn.execute("SELECT payload FROM job WHERE kind = 'fetch'").fetchall()
    assert jobs == [({"url": "https://www.copy.example/a", "reason": "hit", "narrative_id": narrative_id},)]

    conn.execute("UPDATE job SET created_at = now() - interval '25 hours'")
    conn.commit()
    post_hits(client, ("s1", narrative_id, "https://www.copy.example/a"))
    assert conn.execute("SELECT count(*) FROM job WHERE kind = 'fetch'").fetchone()[0] == 2


def test_a_hit_on_an_edited_copy_fetches_the_page_without_an_observation(client, conn, narrative):
    narrative_id = narrative(["s1"])
    post_hits(client, ("edited-block", narrative_id, "https://copy.example/a"))
    assert conn.execute("SELECT count(*) FROM observation").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM job WHERE kind = 'fetch'").fetchone()[0] == 1


def test_a_hit_on_an_unknown_narrative_is_dropped(client, conn):
    post_hits(client, ("s1", 999, "https://copy.example/a"))
    assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == 0


@pytest.mark.parametrize(
    "hit",
    [
        {"hash": "", "narrativeId": "1", "url": "https://a.example/", "seenAt": "2026-11-14T10:00:00Z"},
        {"hash": "s1", "narrativeId": "x", "url": "https://a.example/", "seenAt": "2026-11-14T10:00:00Z"},
        {"hash": "s1", "narrativeId": "1", "url": "file:///etc/passwd", "seenAt": "2026-11-14T10:00:00Z"},
        {"hash": "s1", "narrativeId": "1", "url": "https://a.example/", "seenAt": "yesterday"},
    ],
    ids=["empty hash", "non-numeric narrative", "non-http url", "bad time"],
)
def test_refuses_a_malformed_hit(client, hit):
    assert client.post("/hits", json={"hits": [hit]}).status_code == 422


def refresh(database_url, *views):
    with psycopg.connect(database_url, autocommit=True) as admin:
        for view in views:
            admin.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")


def test_aggregates_count_hits_per_hour_and_new_domains_per_day(conn, narrative, database_url):
    narrative_id = narrative(["s1"], content_hash="fp-1")
    conn.execute("INSERT INTO domain (name) VALUES ('a.example'), ('b.example')")
    conn.execute(
        "INSERT INTO page (url, domain_id) SELECT 'https://' || name || '/' || n, id FROM domain, generate_series(1, 2) n"
    )
    observe = (
        "INSERT INTO observation (seen_at, fingerprint_id, page_id, source)"
        " SELECT date_trunc('hour', now()) - %s::interval, f.id, p.id, %s FROM fingerprint f, page p WHERE p.url = %s"
    )
    conn.execute(observe, ("2 days", "fetch", "https://a.example/1"))
    conn.execute(observe, ("0 days", "detection", "https://a.example/2"))
    conn.execute(observe, ("0 days", "detection", "https://b.example/1"))
    conn.commit()
    refresh(database_url, "narrative_hits_hourly", "narrative_domain_daily")

    hits = conn.execute(
        "SELECT now() - hour < interval '1 hour', source, hits FROM narrative_hits_hourly"
        " WHERE narrative_id = %s ORDER BY hour",
        (narrative_id,),
    ).fetchall()
    assert hits == [(False, "fetch", 1), (True, "detection", 2)]
    new_domains = conn.execute(
        "SELECT new_domains FROM narrative_new_domains_daily WHERE narrative_id = %s ORDER BY day",
        (narrative_id,),
    ).fetchall()
    assert new_domains == [(1,), (1,)]
