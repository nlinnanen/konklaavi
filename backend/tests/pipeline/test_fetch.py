import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from app import fpclient
from app.jobs.fetch import handle
from app.jobs.variants import handle as match_variants

PARAGRAPHS = [
    "The election was rigged by foreign agents according to many anonymous sources today.",
    "Everyone knows the results were changed overnight by the people who hold power in the capital.",
    "Officials have refused to comment on the many reports that surfaced on social media this week.",
]

ARTICLE = f"""<!doctype html>
<html><head>
<title>Rigged election - Example News</title>
<meta property="article:published_time" content="2025-03-04T10:00:00Z">
</head><body>
<nav><a href="/">Home</a> <a href="/about">About us</a></nav>
<article><h1>Rigged election</h1>{"".join(f"<p>{p}</p>" for p in PARAGRAPHS)}</article>
<footer>Copyright Example News</footer>
</body></html>"""


@pytest.fixture
def site():
    """A local web server; tests set routes[path] = (status, content type, body)."""
    routes: dict[str, tuple[int, str, str]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, content_type, body = routes.get(self.path, (404, "text/html", "<p>not found</p>"))
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield routes, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


ELECTION = "Rigged election", "Foreign agents rigged the national election and changed the results overnight."
ON_TOPIC = "The election was rigged by foreign agents according to many anonymous sources today."
OFF_TOPIC = [
    "The local football club won its third match in a row after a late goal in the second half.",
    "Subscribe to our newsletter to receive the best recipes and gardening tips every Sunday morning.",
]


def page(*paragraphs: str) -> str:
    return f"<html><body><article>{''.join(f'<p>{p}</p>' for p in paragraphs)}</article></body></html>"


def add_narrative(conn, title, claim):
    return conn.execute(
        "INSERT INTO narrative (title, claim, source) VALUES (%s, %s, 'euvsdisinfo') RETURNING id",
        (title, claim),
    ).fetchone()[0]


def attached(conn, narrative_id):
    return {
        row[0]
        for row in conn.execute(
            "SELECT f.content FROM fingerprint f JOIN variant v ON v.id = f.variant_id WHERE v.narrative_id = %s",
            (narrative_id,),
        )
    }


def stored_sentences(conn):
    return {row[0] for row in conn.execute("SELECT hash FROM sentence")}


def test_public_article_is_stored_with_the_services_fingerprints(conn, site):
    routes, base = site
    routes["/article"] = (200, "text/html; charset=utf-8", ARTICLE)

    handle(conn, {"url": f"{base}/article", "reason": "seed"})

    expected = fpclient.fingerprint(PARAGRAPHS)["blocks"]
    status, title, published = conn.execute(
        "SELECT status, title, published_at::date::text FROM page"
    ).fetchone()
    assert (status, title, published) == ("ok", "Rigged election", "2025-03-04")
    assert conn.execute("SELECT name FROM domain").fetchone()[0] == "127.0.0.1"
    stored = conn.execute("SELECT content_hash, minhash, embedding IS NOT NULL FROM fingerprint").fetchall()
    assert sorted(stored) == sorted((b["contentHash"], b["minhash"], True) for b in expected)
    assert stored_sentences(conn) == {s["hash"] for b in expected for s in b["sentences"]}
    bands = conn.execute(
        "SELECT f.content_hash, b.band, b.hash FROM lsh_band b JOIN fingerprint f ON f.id = b.fingerprint_id"
    ).fetchall()
    assert sorted(bands) == sorted(
        (b["contentHash"], i, h) for b in expected for i, h in enumerate(b["bands"])
    )
    sources = conn.execute("SELECT source, count(*) FROM observation GROUP BY source").fetchall()
    assert sources == [("fetch", len(PARAGRAPHS))]


@pytest.mark.parametrize("status, content_type", [(404, "text/html"), (200, "application/pdf")])
def test_non_public_page_stores_no_text(conn, site, status, content_type):
    routes, base = site
    routes["/gone"] = (status, content_type, ARTICLE)

    handle(conn, {"url": f"{base}/gone", "reason": "vote"})

    assert conn.execute("SELECT status FROM page").fetchone()[0] == "private"
    assert conn.execute("SELECT count(*) FROM fingerprint").fetchone()[0] == 0


def test_refetching_the_same_content_keeps_one_fingerprint_per_block(conn, site):
    routes, base = site
    routes["/a"] = (200, "text/html", ARTICLE)
    routes["/copy"] = (200, "text/html", ARTICLE)

    handle(conn, {"url": f"{base}/a", "reason": "seed"})
    conn.commit()
    first = conn.execute("SELECT id, first_seen, first_page_id FROM fingerprint ORDER BY id").fetchall()
    handle(conn, {"url": f"{base}/a", "reason": "hit"})
    conn.commit()
    handle(conn, {"url": f"{base}/copy", "reason": "hit"})

    assert (
        conn.execute("SELECT id, first_seen, first_page_id FROM fingerprint ORDER BY id").fetchall() == first
    )
    assert conn.execute("SELECT count(*) FROM page").fetchone()[0] == 2


def test_new_fingerprints_are_queued_for_variant_matching(conn, site):
    routes, base = site
    routes["/a"] = (200, "text/html", ARTICLE)

    handle(conn, {"url": f"{base}/a", "reason": "seed"})
    conn.commit()
    handle(conn, {"url": f"{base}/a", "reason": "hit"})

    jobs = conn.execute("SELECT payload FROM job WHERE kind = 'match_variants'").fetchall()
    ids = [row[0] for row in conn.execute("SELECT id FROM fingerprint ORDER BY id")]
    assert [sorted(payload["fingerprint_ids"]) for (payload,) in jobs] == [ids]


def test_refetch_fills_the_signature_of_a_fingerprint_stored_without_one(conn, site):
    routes, base = site
    routes["/a"] = (200, "text/html", page(ON_TOPIC))
    content_hash = fpclient.fingerprint([ON_TOPIC])["blocks"][0]["contentHash"]
    conn.execute("INSERT INTO fingerprint (content_hash, content) VALUES (%s, 'excerpt')", (content_hash,))

    handle(conn, {"url": f"{base}/a", "reason": "hit"})

    assert conn.execute(
        "SELECT minhash IS NOT NULL, embedding IS NOT NULL, (SELECT count(*) FROM lsh_band) FROM fingerprint"
    ).fetchone() == (True, True, 32)


def test_only_blocks_relevant_to_the_narrative_are_attached(conn, site):
    routes, base = site
    routes["/article"] = (200, "text/html", page(ON_TOPIC, *OFF_TOPIC))
    narrative_id = add_narrative(conn, *ELECTION)

    handle(conn, {"url": f"{base}/article", "reason": "seed", "narrative_id": narrative_id})

    assert attached(conn, narrative_id) == {ON_TOPIC}


def test_the_closest_block_is_attached_when_none_is_relevant(conn, site):
    routes, base = site
    routes["/article"] = (200, "text/html", page(ON_TOPIC, *OFF_TOPIC))
    narrative_id = add_narrative(
        conn, "Healthy pasta", "Pasta cooked with fresh tomatoes and basil is the healthiest dinner at home."
    )

    handle(conn, {"url": f"{base}/article", "reason": "seed", "narrative_id": narrative_id})

    assert attached(conn, narrative_id) == {OFF_TOPIC[1]}


def test_narrative_centroid_is_the_mean_of_its_attached_embeddings(conn, site):
    routes, base = site
    routes["/article"] = (200, "text/html", ARTICLE)
    narrative_id = add_narrative(conn, *ELECTION)

    handle(conn, {"url": f"{base}/article", "reason": "seed", "narrative_id": narrative_id})

    embeddings = [
        row[0]
        for row in conn.execute(
            "SELECT f.embedding::real[] FROM fingerprint f JOIN variant v ON v.id = f.variant_id WHERE v.narrative_id = %s",
            (narrative_id,),
        )
    ]
    centroid = conn.execute(
        "SELECT centroid::real[] FROM narrative WHERE id = %s", (narrative_id,)
    ).fetchone()[0]
    assert embeddings
    assert np.allclose(centroid, np.mean(embeddings, axis=0), atol=1e-6)


def test_a_near_duplicate_on_two_pages_of_a_domain_is_detached_as_boilerplate(conn, site):
    routes, base = site
    teaser = "Updated {}: read our full investigation into how foreign agents rigged the national election."
    second = "Anonymous sources say foreign agents rigged the election and changed the results overnight."
    routes["/first"] = (200, "text/html", page(ON_TOPIC, teaser.format("12 March 2025")))
    routes["/second"] = (200, "text/html", page(second, teaser.format("3 April 2025")))
    narrative_id = add_narrative(conn, *ELECTION)

    handle(conn, {"url": f"{base}/first", "reason": "seed", "narrative_id": narrative_id})
    assert attached(conn, narrative_id) == {ON_TOPIC, teaser.format("12 March 2025")}
    handle(conn, {"url": f"{base}/second", "reason": "seed", "narrative_id": narrative_id})

    assert attached(conn, narrative_id) == {ON_TOPIC, second}


def test_a_short_line_is_never_attached(conn, site):
    routes, base = site
    short = "Foreign agents rigged the election."
    routes["/article"] = (200, "text/html", page(short, ON_TOPIC))
    narrative_id = add_narrative(conn, *ELECTION)

    handle(conn, {"url": f"{base}/article", "reason": "seed", "narrative_id": narrative_id})

    assert attached(conn, narrative_id) == {ON_TOPIC}


def test_a_narrative_left_without_blocks_gets_the_next_best_block_of_its_pages(conn, site):
    routes, base = site
    routes["/first"] = (200, "text/html", page(ON_TOPIC, OFF_TOPIC[0]))
    routes["/other"] = (200, "text/html", page(ON_TOPIC, OFF_TOPIC[1]))
    narrative_id = add_narrative(conn, *ELECTION)

    handle(conn, {"url": f"{base}/first", "reason": "seed", "narrative_id": narrative_id})
    assert attached(conn, narrative_id) == {ON_TOPIC}
    handle(conn, {"url": f"{base}/other", "reason": "vote"})

    assert attached(conn, narrative_id) == {OFF_TOPIC[0]}


def test_a_hit_refetches_only_a_page_not_fetched_in_the_last_day(conn, site):
    routes, base = site
    routes["/a"] = (200, "text/html", page(ON_TOPIC))
    handle(conn, {"url": f"{base}/a", "reason": "seed"})
    routes["/a"] = (200, "text/html", page(ON_TOPIC, OFF_TOPIC[0]))

    handle(conn, {"url": f"{base}/a", "reason": "hit"})
    assert conn.execute("SELECT count(*) FROM fingerprint").fetchone()[0] == 1

    conn.execute("UPDATE page SET fetched_at = now() - interval '25 hours'")
    handle(conn, {"url": f"{base}/a", "reason": "hit"})
    assert conn.execute("SELECT count(*) FROM fingerprint").fetchone()[0] == 2


def test_a_hit_page_joins_the_hits_narrative_and_derives_from_the_seed(conn, site):
    routes, base = site
    original = (
        "Western sanctions against Russia have backfired and are destroying the European economy, while"
        " ordinary citizens in Germany and France pay record prices for gas and electricity."
    )
    edited = original.replace("record prices", "insane prices")
    routes["/seed"] = (200, "text/html", page(original, OFF_TOPIC[0]))
    routes["/copy"] = (200, "text/html", page(edited, OFF_TOPIC[1]))
    narrative_id = add_narrative(
        conn, "Sanctions backfire", "Western sanctions against Russia are destroying the European economy."
    )
    handle(conn, {"url": f"{base}/seed", "reason": "seed", "narrative_id": narrative_id})
    conn.execute("DELETE FROM job")

    # Another domain for the same server, so the copy is not boilerplate of the seed's domain.
    copy_url = f"{base.replace('127.0.0.1', 'localhost')}/copy"
    handle(conn, {"url": copy_url, "reason": "hit", "narrative_id": narrative_id})
    [(payload,)] = conn.execute("SELECT payload FROM job WHERE kind = 'match_variants'").fetchall()
    match_variants(conn, payload)

    assert attached(conn, narrative_id) == {original, edited}
    assert conn.execute(
        "SELECT s.content, d.content FROM edge e"
        " JOIN fingerprint s ON s.id = e.src JOIN fingerprint d ON d.id = e.dst WHERE e.type = 'DERIVED_FROM'"
    ).fetchall() == [(edited, original)]
