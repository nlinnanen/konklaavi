import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import fpclient
from app.jobs.fetch import handle

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


def stored_sentences(conn):
    return {row[0] for row in conn.execute("SELECT hash FROM sentence")}


def test_public_article_is_stored_with_the_services_sentence_hashes(conn, site):
    routes, base = site
    routes["/article"] = (200, "text/html; charset=utf-8", ARTICLE)

    handle(conn, {"url": f"{base}/article", "reason": "seed"})

    expected = fpclient.fingerprint(PARAGRAPHS)["blocks"]
    status, title, published = conn.execute(
        "SELECT status, title, published_at::date::text FROM page"
    ).fetchone()
    assert (status, title, published) == ("ok", "Rigged election", "2025-03-04")
    assert conn.execute("SELECT name FROM domain").fetchone()[0] == "127.0.0.1"
    assert {row[0] for row in conn.execute("SELECT content_hash FROM fingerprint")} == {
        b["contentHash"] for b in expected
    }
    assert stored_sentences(conn) == {s["hash"] for b in expected for s in b["sentences"]}
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


def test_fingerprints_are_attached_to_the_payloads_narrative(conn, site):
    routes, base = site
    routes["/article"] = (200, "text/html", ARTICLE)
    narrative_id = conn.execute(
        "INSERT INTO narrative (title, source) VALUES ('Rigged election', 'euvsdisinfo') RETURNING id"
    ).fetchone()[0]

    handle(conn, {"url": f"{base}/article", "reason": "seed", "narrative_id": narrative_id})

    rows = conn.execute(
        "SELECT v.narrative_id FROM fingerprint f LEFT JOIN variant v ON v.id = f.variant_id"
    ).fetchall()
    assert rows == [(narrative_id,)] * len(PARAGRAPHS)
