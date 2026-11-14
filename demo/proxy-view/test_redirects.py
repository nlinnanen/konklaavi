"""Redirects and navigations are checked hop by hop, in the renderer and in the plain fetch.

A local server plays both sides: `localhost` is the allowlisted site and `127.0.0.1` the internal
address an open redirect points at, which must never be requested. The renderer tests start
Chromium (downloaded on first use). From the repository root:

    uv run --with pytest --with-requirements demo/proxy-view/viewer.py pytest demo/proxy-view
"""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import render
import viewer

PAGE = "<!doctype html><title>ok</title><p>This is the allowed page with enough words in it.</p>"


class Site(BaseHTTPRequestHandler):
    internal_hits = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        host = self.headers.get("host", "").split(":")[0]
        port = self.server.server_address[1]
        if host != "localhost":
            type(self).internal_hits += 1
            self.reply(200, "<p>internal secret</p>")
        elif url.path == "/page":
            self.reply(200, PAGE)
        elif url.path == "/redirect":
            # An open redirect: goes wherever ?to= says, relative or absolute.
            to = parse_qs(url.query)["to"][0].replace("PORT", str(port))
            self.send_response(302)
            self.send_header("location", to)
            self.end_headers()
        elif url.path == "/loop":
            self.send_response(302)
            self.send_header("location", "/loop")
            self.end_headers()
        elif url.path == "/meta":
            self.reply(200, f"<meta http-equiv=refresh content='0;url=http://127.0.0.1:{port}/'><p>{PAGE}</p>")
        elif url.path == "/script":
            self.reply(200, f"<script>location = 'http://127.0.0.1:{port}/'</script>{PAGE}")
        elif url.path == "/script-ok":
            self.reply(200, "<script>location = '/redirect?to=/page'</script>")
        else:
            self.reply(404, "missing")

    def reply(self, status: int, body: str):
        data = body.encode()
        self.send_response(status)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def port():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


@pytest.fixture(autouse=True)
def reset_hits():
    Site.internal_hits = 0


def only_localhost(url: str) -> str | None:
    return None if urlparse(url).hostname == "localhost" else "not allowed"


def site(port: int, path: str) -> str:
    return f"http://localhost:{port}{path}"


# ---- the plain fetch ----


@pytest.fixture
def fetch_rules(monkeypatch):
    monkeypatch.setattr(viewer, "hop_reason", only_localhost)


def test_fetch_follows_allowed_redirects(port, fetch_rules):
    result = viewer.fetch(site(port, "/redirect?to=/page"))
    assert isinstance(result, render.Rendered)
    assert result.url == site(port, "/page")


@pytest.mark.parametrize(
    "path",
    [
        "/redirect?to=http://127.0.0.1:PORT/",
        "/redirect?to=/redirect%3Fto%3Dhttp://127.0.0.1:PORT/",  # two hops, the second one out
    ],
)
def test_fetch_refuses_redirects_off_the_list(port, fetch_rules, path):
    result = viewer.fetch(site(port, path))
    assert not isinstance(result, render.Rendered)
    assert result.status_code == 400
    assert Site.internal_hits == 0


def test_fetch_stops_redirect_loops(port, fetch_rules):
    result = viewer.fetch(site(port, "/loop"))
    assert not isinstance(result, render.Rendered)


# ---- the renderer ----


def run(url: str) -> render.Rendered:
    try:
        return asyncio.run(render.render(url, only_localhost))
    finally:
        render._browser = None  # its connection belongs to this asyncio.run's event loop


@pytest.fixture(scope="module")
def chromium():
    try:
        asyncio.run(render.browser())
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Chromium is not available: {e}")
    render._browser = None


def test_render_follows_allowed_redirects(port, chromium):
    result = run(site(port, "/redirect?to=/redirect%3Fto%3D/page"))
    assert result.url == site(port, "/page")
    assert "allowed page" in result.html


def test_render_follows_allowed_script_navigations(port, chromium):
    result = run(site(port, "/script-ok"))
    assert result.url == site(port, "/page")


@pytest.mark.parametrize(
    "path",
    [
        "/redirect?to=http://127.0.0.1:PORT/",
        "/redirect?to=/redirect%3Fto%3Dhttp://127.0.0.1:PORT/",
        "/loop",
        "/script",  # a script that navigates while the page loads stops the load: a redirect, in effect
    ],
)
def test_render_refuses_redirects_off_the_list(port, chromium, path):
    with pytest.raises(render.Refused):
        run(site(port, path))
    assert Site.internal_hits == 0


def test_render_stops_navigations_off_the_list(port, chromium):
    path = "/meta"  # a meta refresh navigates once the page has loaded: the page stays
    result = run(site(port, path))
    assert result.url == site(port, path)
    assert "internal secret" not in result.html
    assert Site.internal_hits == 0
