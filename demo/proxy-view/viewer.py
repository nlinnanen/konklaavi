# /// script
# requires-python = ">=3.12"
# dependencies = ["fastapi", "uvicorn", "httpx", "trafilatura", "playwright"]
# ///
"""Konklaavi proxy view: shows a page with what the extension would flag, without the extension.

The server renders the page in headless Chromium (render.py), so comments and other content its
JavaScript loads are there, freezes it into static HTML without scripts, keeps its CSS and images
through <base href>, and injects overlay.bundle.js: the browser extension's highlights, cards, note form and sign-in
(src/overlay.ts with the extension's modules, built by build.mjs, which needs `npm install` in
client/). It sends the visible text, block by block, to the Konklaavi API's /check, highlights the
matches in place, and forwards the extension's other API calls through /api/. Without a browser it falls back to
a plain fetch and the main text that trafilatura extracts. The first run downloads Chromium.

    uv run demo/proxy-view/viewer.py                       # uses the deployed API
    KONKLAAVI_API=http://localhost:8000 uv run demo/proxy-view/viewer.py
    open http://localhost:8787

Behind a reverse proxy under a path, set PUBLIC_BASE to the public address of that path
(https://example.org/demo/): links in proxied pages must be absolute, since each page's
<base href> points at the original site.
"""

import asyncio
import html
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx
import lxml.html
import render
import trafilatura
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

# HASHTRUST_API is the name before the rename, which a server not yet reconfigured still sets.
API = os.environ.get("KONKLAAVI_API", os.environ.get("HASHTRUST_API", "https://konklaavi.tech/api")).rstrip("/")
PORT = int(os.environ.get("PORT", "8787"))
HERE = Path(__file__).parent
UA = render.UA
# Set to 0 to skip the browser and use the plain fetch.
RENDER = os.environ.get("KONKLAAVI_RENDER", os.environ.get("HASHTRUST_RENDER", "1")) != "0"
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "").rstrip("/")
# Where the extension's install instructions are.
README_URL = os.environ.get("README_URL", "https://github.com/nlinnanen/konklaavi#try-it")
NOTICE = (
    "This demo renders the page again on our server, without its scripts, and shows what the Konklaavi "
    "browser extension would highlight on it. It may not work on every site: some block automated "
    "browsers or need their scripts to show their content."
)
PRODUCT = (
    f"The real product is the <b>browser extension</b>, which checks pages as you read them, on your own "
    f"device. <a href='{html.escape(README_URL, quote=True)}' target=_blank rel=noopener>How to install it</a>."
)
# Sites the proxy opens, with their subdomains. ALLOWED_DOMAINS (comma-separated) replaces the
# list, and "*" allows any public site, for local testing.
DEFAULT_DOMAINS = [
    "reddit.com", "redd.it", "wikipedia.org",
    # Finnish news
    "yle.fi", "hs.fi", "is.fi", "iltalehti.fi", "mtvuutiset.fi", "kauppalehti.fi", "talouselama.fi",
    "aamulehti.fi", "ts.fi", "kaleva.fi", "ksml.fi", "savonsanomat.fi", "satakunnankansa.fi",
    "uusisuomi.fi", "verkkouutiset.fi", "maaseuduntulevaisuus.fi", "hbl.fi", "vasabladet.fi",
    "suomenuutiset.fi", "apu.fi", "seura.fi",
    # International news
    "bbc.com", "bbc.co.uk", "theguardian.com", "reuters.com", "apnews.com", "nytimes.com",
    "washingtonpost.com", "wsj.com", "ft.com", "economist.com", "bloomberg.com", "cnn.com",
    "nbcnews.com", "cbsnews.com", "abcnews.go.com", "npr.org", "politico.com", "politico.eu", "axios.com",
    "theatlantic.com", "time.com", "usatoday.com", "latimes.com", "independent.co.uk", "telegraph.co.uk",
    "thetimes.com", "aljazeera.com", "dw.com", "france24.com", "euronews.com", "lemonde.fr", "spiegel.de",
    "zeit.de", "faz.net", "elpais.com", "corriere.it", "abc.net.au", "cbc.ca", "scmp.com",
    "kyivindependent.com", "meduza.io", "rferl.org", "euobserver.com",
    # Nordic and Baltic public broadcasters and dailies
    "svt.se", "dn.se", "nrk.no", "dr.dk", "err.ee", "lrt.lt", "lsm.lv",
]
_allowed = os.environ.get("ALLOWED_DOMAINS", "").strip()
ALLOWED = None if _allowed == "*" else [d.strip().lower() for d in _allowed.split(",") if d.strip()] or DEFAULT_DOMAINS


def allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return ALLOWED is None or any(host == d or host.endswith("." + d) for d in ALLOWED)

# Rendered pages are kept this long, so opening one again only runs the check.
CACHE_SECONDS = 60 * float(os.environ.get("CACHE_MINUTES", "15"))
CACHE_SIZE = 50
# Pages to render at startup and keep fresh, comma-separated, such as the articles a demo will show.
PREWARM = [u.strip() for u in os.environ.get("PREWARM_URLS", "").split(",") if u.strip()]
# Pages rendered at once; each is a Chromium tab.
RENDERS = asyncio.Semaphore(int(os.environ.get("MAX_RENDERS", "2")))

_cache: dict[str, tuple[float, render.Rendered]] = {}


async def rendered(url: str, fresh: bool = False) -> tuple[render.Rendered, str]:
    """The rendered page, from the cache when it is recent, and how it was got."""
    hit = _cache.get(url)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1], f"rendered {int((time.monotonic() - hit[0]) // 60)} min ago"
    async with RENDERS:
        result = await render.render(url)
    if result.status < 400:
        _cache.pop(url, None)
        _cache[url] = (time.monotonic(), result)
        while len(_cache) > CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
    return result, "rendered"


async def prewarm() -> None:
    """Renders PREWARM_URLS now and again before they expire."""
    while True:
        for url in PREWARM:
            try:
                await rendered(url, fresh=True)
            except Exception as e:  # noqa: BLE001 - a failing page must not stop the others
                print(f"prewarm failed for {url}: {e!r}", flush=True)
        await asyncio.sleep(max(60, CACHE_SECONDS * 0.8))


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(prewarm()) if PREWARM and RENDER else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Konklaavi proxy view", lifespan=lifespan)


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{(HERE / 'home.css').read_text()}</style>{body}"
    )


def proxy_base(request: Request) -> str:
    """The proxy's own address, ending in a slash, for the links it writes into pages."""
    return PUBLIC_BASE + "/" if PUBLIC_BASE else str(request.base_url)


def private_reason(url: str) -> str | None:
    """Why the proxy refuses a URL: only public http(s) hosts, so it can't reach internal services."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Only http(s) URLs can be opened."
    if not allowed(url):
        return f"{parsed.hostname} is not one of the sites this demo opens: {', '.join(ALLOWED)}."
    # Behind an HTTP proxy without DNS (some sandboxes), the address check can't run.
    if os.environ.get("SKIP_ADDRESS_CHECK") == "1":
        return None
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except OSError:
        return f"Could not resolve {parsed.hostname}."
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            return f"{parsed.hostname} is not a public address."
    return None


def form(url: str = "") -> str:
    return (
        "<form action=view target=_top><input name=url type=url required placeholder='https://… article URL' "
        f"value='{html.escape(url, quote=True)}' autofocus><button>Scan</button></form>"
    )


@app.get("/", response_class=HTMLResponse)
def home():
    return page(
        "Konklaavi demo",
        "<main><h1>Konklaavi <span>demo</span></h1><p class=lede>Open an article and see which passages "
        f"in it and its comments belong to a tracked narrative, and what readers' notes say.</p>{form()}"
        + (f"<p class=meta>Works with {html.escape(', '.join(ALLOWED))}.</p>" if ALLOWED else "")
        + f"<div class=notice><p>{html.escape(NOTICE)}</p><p>{PRODUCT}</p></div>"
        + f"<p class=meta>API: <code>{html.escape(API)}</code></p></main>",
    )


def error(url: str, message: str, status: int = 502) -> HTMLResponse:
    response = page(
        "Could not load page",
        f"<main><h1>Could not load the page</h1><p class=err>{html.escape(message)}</p>{form(url)}</main>",
    )
    response.status_code = status
    return response


def rewrite(doc: lxml.html.HtmlElement, base: str, proxy: str) -> None:
    """Make the page inert and self-contained: no scripts, links stay in the proxy, lazy images load."""
    for el in doc.xpath("//script | //iframe | //object | //embed | //meta[@http-equiv]"):
        el.drop_tree()
    # Lazy-loaded images only show their real source through JS; noscript copies would duplicate them.
    for el in doc.xpath("//noscript"):
        el.drop_tag() if not el.xpath(".//img") else el.drop_tree()
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if attr.startswith("on"):
                del el.attrib[attr]
        if el.tag in ("img", "source"):
            for lazy, real in (("data-src", "src"), ("data-lazy-src", "src"), ("data-srcset", "srcset")):
                if el.get(lazy):
                    el.set(real, el.get(lazy))
            el.attrib.pop("loading", None)
            # DW: <img data-url=".../123_${formatId}.jpg" style="height:0"> filled in by its JS.
            if not el.get("src") and "${formatId}" in el.get("data-url", ""):
                el.set("src", el.get("data-url").replace("${formatId}", "906"))
                el.attrib.pop("style", None)
        if el.tag == "a" and el.get("href"):
            target = urljoin(base, el.get("href"))
            if target.startswith("http") and allowed(target):
                el.set("href", f"{proxy}view?url=" + quote(target, safe=""))
                el.set("target", "_top")
            elif target.startswith("http"):
                # Sites outside the allowlist open directly, outside the proxy.
                el.set("href", target)
                el.set("target", "_blank")
                el.set("rel", "noopener")
        if el.tag == "form":
            el.set("action", "javascript:void 0")


SHELL = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Konklaavi demo</title>
<style>
:root{{--ink:oklch(.24 .012 70);--ink-2:oklch(.43 .012 70);--ink-3:oklch(.55 .01 70);--paper:oklch(.992 .003 85);
--paper-2:oklch(.966 .006 85);--paper-3:oklch(.935 .008 85);--line:oklch(.9 .008 85)}}
@media (prefers-color-scheme:dark){{:root{{--ink:oklch(.94 .006 85);--ink-2:oklch(.79 .01 85);--ink-3:oklch(.66 .01 85);
--paper:oklch(.235 .008 70);--paper-2:oklch(.275 .009 70);--paper-3:oklch(.31 .01 70);--line:oklch(.36 .01 70)}}}}
html,body{{margin:0;height:100%;overflow:hidden;background:var(--paper)}}
iframe{{position:fixed;top:48px;left:0;width:100%;height:calc(100% - 48px);border:0;background:#fff;opacity:0;transition:opacity .3s}}
#ht-banner{{position:fixed;top:0;left:0;right:0;height:48px;box-sizing:border-box;padding:0 16px;
display:flex;align-items:center;gap:10px;background:var(--paper-2);color:var(--ink-2);border-bottom:1px solid var(--line);
font:13px/1.3 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;white-space:nowrap;overflow:hidden}}
#ht-banner b{{font-size:11px;font-weight:700;letter-spacing:.08em;padding:3px 7px;border-radius:5px;background:var(--ink);color:var(--paper)}}
#ht-banner span{{overflow:hidden;text-overflow:ellipsis}}
#ht-banner a{{margin-left:auto;color:var(--ink);font-weight:600;text-underline-offset:2px}}
iframe.ready{{opacity:1}}
#ht-loading{{position:fixed;inset:0;display:grid;place-items:center;background:var(--paper);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}}
.box{{width:min(420px,calc(100vw - 32px));text-align:center}}
.ring{{width:44px;height:44px;margin:0 auto 18px;border-radius:50%;border:3px solid var(--line);
border-top-color:var(--ink-2);animation:spin .9s linear infinite}}
h1{{margin:0;font-size:17px;font-weight:650}} h1 span{{color:var(--ink-3);font-weight:400}}
.host{{margin:6px 0 18px;color:var(--ink-2);font-size:13px;overflow-wrap:anywhere}}
ol{{list-style:none;margin:0 auto;padding:0;display:grid;gap:8px;width:fit-content;text-align:left;font-size:13px;color:var(--ink-3)}}
li{{display:flex;align-items:center;gap:10px;transition:color .3s}}
li::before{{content:"";width:8px;height:8px;border-radius:50%;background:var(--line);transition:background .3s}}
li.on{{color:var(--ink)}} li.on::before{{background:var(--ink-2);animation:pulse 1s ease-in-out infinite}}
li.done{{color:var(--ink-2)}} li.done::before{{background:var(--ink-3);animation:none}}
.note{{margin:22px auto 0;max-width:380px;font-size:12px;line-height:1.5;color:var(--ink-3)}}
.note a{{color:inherit}}
.time{{margin-top:18px;font-size:12px;color:var(--ink-3);font-variant-numeric:tabular-nums}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes pulse{{50%{{opacity:.35}}}}
@media (prefers-reduced-motion:reduce){{.ring,li.on::before{{animation:none}}}}
</style>
<div id=ht-banner role=note><b>DEMO</b><span>A copy of {host} rendered by Konklaavi. Links, videos and logins on it may not work.</span>
<a href="{readme}" target=_blank rel=noopener>The real product is the browser extension →</a></div>
<iframe id=ht-frame src="{src}"></iframe>
<div id=ht-loading role=status aria-live=polite><div class=box>
<div class=ring></div>
<h1>Konklaavi <span>demo</span></h1>
<p class=host>{host}</p>
<ol><li>Opening the page</li><li>Accepting cookies</li><li>Loading comments and replies</li>
<li>Reading the text</li><li>Matching against tracked narratives</li></ol>
<p class=time>0 s</p>
<p class=note>{notice}</p>
</div></div>
<script>
// Rendering happens on the server in one request; the steps advance on a typical schedule, not on progress.
const items = [...document.querySelectorAll('#ht-loading li')], at = [0, 1, 2.5, 4.5, 6];
const t0 = Date.now(), time = document.querySelector('#ht-loading .time');
const tick = () => {{
  const s = (Date.now() - t0) / 1000;
  if (time) time.textContent = Math.floor(s) + ' s';
  items.forEach((li, i) => {{
    const next = at[i + 1] ?? Infinity;
    li.className = s >= next ? 'done' : s >= at[i] ? 'on' : '';
  }});
  if (document.getElementById('ht-loading')) setTimeout(tick, 250);
}};
tick();
// The overlay removes the loading screen; an error page has no overlay, so the frame's load does too.
document.getElementById('ht-frame').addEventListener('load', (e) => {{
  e.target.classList.add('ready');
  document.getElementById('ht-loading')?.remove();
}});
</script>"""


@app.get("/view", response_class=HTMLResponse)
def view(url: str, test: bool = False, fresh: bool = False):
    """The shell: the page in a same-origin frame left of the panel, so its layout gets the real width."""
    src = "frame?url=" + quote(url, safe="") + ("&test=1" if test else "") + ("&fresh=1" if fresh else "")
    return SHELL.format(
        src=html.escape(src, quote=True),
        host=html.escape(urlparse(url).hostname or url),
        readme=html.escape(README_URL, quote=True),
        notice="It may not work on every site. The real product is the browser extension: "
        f"<a href='{html.escape(README_URL, quote=True)}' target=_blank rel=noopener>how to install it</a>.",
    )


def fetch(url: str) -> render.Rendered | HTMLResponse:
    """The page as served, without running its JavaScript, and its main text."""
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=20,
            headers={"user-agent": UA, "accept-language": "en,fi;q=0.8"},
        )
    except httpx.HTTPError as e:
        return error(url, f"Fetch failed: {e}")
    if response.status_code >= 400:
        return error(url, f"The site answered HTTP {response.status_code} (often bot protection).")
    if "html" not in response.headers.get("content-type", "html"):
        return error(url, f"Not an HTML page ({response.headers.get('content-type')}).", 415)
    final = str(response.url)
    text = trafilatura.extract(response.text, url=final, favor_recall=True) or ""
    blocks = [line.strip() for line in text.splitlines() if line.strip()]
    return render.Rendered(url=final, status=response.status_code, html=response.text, blocks=blocks)


@app.get("/frame")
async def frame(request: Request, url: str, test: bool = False, fresh: bool = False):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if reason := await asyncio.to_thread(private_reason, url):
        return error(url, reason, 400)
    return await frame_page(request, url, test, fresh)


async def frame_page(request: Request, url: str, test: bool, fresh: bool):
    result = None
    mode = "plain fetch"
    if RENDER:
        try:
            result, mode = await rendered(url, fresh)
        except Exception as e:  # noqa: BLE001 - any browser failure falls back to the plain fetch
            print(f"render failed for {url}: {e!r}; falling back to a plain fetch", flush=True)
    if result is None or result.status >= 400:
        if result is not None:
            print(f"render got HTTP {result.status} for {url}; trying a plain fetch", flush=True)
        fetched = await asyncio.to_thread(fetch, url)
        if isinstance(fetched, HTMLResponse):
            return fetched
        result, mode = fetched, "plain fetch"
    final = result.url
    blocks = result.blocks

    doc = lxml.html.document_fromstring(result.html)
    rewrite(doc, final, proxy_base(request))
    head = doc.find("head")
    if head is None:
        head = lxml.html.Element("head")
        doc.insert(0, head)
    for old in head.findall("base"):
        old.drop_tree()
    base = lxml.html.Element("base", href=final)
    head.insert(0, base)

    config = {
        "url": final,
        "host": urlparse(final).hostname,
        "blocks": blocks,
        "status": result.status,
        "mode": mode,
        "test": test,
        "proxy": proxy_base(request),
        "readme": README_URL,
    }
    data = lxml.html.fragment_fromstring(
        "<script id=konklaavi-data type=application/json></script>", create_parent=False
    )
    # </script> inside the JSON would end the element early.
    data.text = json.dumps(config).replace("</", "<\\/")
    script = lxml.html.Element("script")
    script.text = overlay_script()
    body = doc.find("body")
    if body is None:
        body = doc
    body.append(data)
    body.append(script)
    return HTMLResponse("<!doctype html>\n" + lxml.html.tostring(doc, encoding="unicode"))


# The API calls the overlay makes, as the extension's background worker would: path pattern and method.
API_ROUTES = [
    (re.compile(r"check"), "POST"),
    (re.compile(r"narratives/\d+"), "GET"),
    (re.compile(r"notes"), "POST"),
    (re.compile(r"notes/\d+/(ratings|report)"), "POST"),
]


@app.api_route("/api/{path:path}", methods=["GET", "POST"])
async def api(request: Request, path: str):
    """Same-origin forward to the API, so the page's CSP or CORS never gets in the way."""
    if not any(pattern.fullmatch(path) and method == request.method for pattern, method in API_ROUTES):
        return JSONResponse({"error": f"{request.method} /{path} is not forwarded"}, status_code=404)
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            r = await client.request(
                request.method,
                f"{API}/{path}",
                params=request.query_params,
                content=await request.body(),
                headers={"content-type": "application/json"},
            )
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"API unreachable: {e}"}, status_code=502)
    if r.headers.get("content-type", "").startswith("application/json"):
        return JSONResponse(r.json(), status_code=r.status_code)
    return JSONResponse({"error": r.text[:500]}, status_code=r.status_code)


def overlay_script() -> str:
    """overlay.bundle.js, built from src/ with the extension's modules when it is missing or older."""
    bundle = HERE / "overlay.bundle.js"
    sources = [*(HERE / "src").glob("*.ts"), *(HERE.parent.parent / "client" / "entrypoints" / "content").glob("*.ts")]
    newest = max((p.stat().st_mtime for p in sources), default=0)
    if not bundle.exists() or bundle.stat().st_mtime < newest:
        try:
            subprocess.run(["node", str(HERE / "build.mjs")], check=True, cwd=HERE.parent.parent / "client")
        except (OSError, subprocess.CalledProcessError) as e:
            if not bundle.exists():
                raise RuntimeError(
                    "overlay.bundle.js is missing and could not be built: run `npm install` in client/ "
                    "and `node demo/proxy-view/build.mjs` from client/"
                ) from e
    return bundle.read_text()


if __name__ == "__main__":
    overlay_script()
    print(f"Konklaavi proxy view on http://localhost:{PORT}  (API {API})")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
