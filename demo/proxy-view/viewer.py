# /// script
# requires-python = ">=3.12"
# dependencies = ["fastapi", "uvicorn", "httpx", "trafilatura", "playwright"]
# ///
"""HashTrust proxy view: shows a page with what the extension would flag, without the extension.

The server renders the page in headless Chromium (render.py), so comments and other content its
JavaScript loads are there, freezes it into static HTML without scripts, keeps its CSS and images
through <base href>, and injects a scanner. The scanner sends the visible text, block by block, to
the HashTrust API's /check and highlights the matches in place. Without a browser it falls back to
a plain fetch and the main text that trafilatura extracts. The first run downloads Chromium.

    uv run demo/proxy-view/viewer.py                       # uses the deployed API
    HASHTRUST_API=http://localhost:8000 uv run demo/proxy-view/viewer.py
    open http://localhost:8787

Behind a reverse proxy under a path, set PUBLIC_BASE to the public address of that path
(https://example.org/proxy/): links in proxied pages must be absolute, since each page's
<base href> points at the original site.
"""

import asyncio
import html
import ipaddress
import json
import os
import socket
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx
import lxml.html
import render
import trafilatura
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

API = os.environ.get("HASHTRUST_API", "https://srv2160986.hstgr.cloud/api").rstrip("/")
PORT = int(os.environ.get("PORT", "8787"))
HERE = Path(__file__).parent
UA = render.UA
# Set to 0 to skip the browser and use the plain fetch.
RENDER = os.environ.get("HASHTRUST_RENDER", "1") != "0"
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "").rstrip("/")
# Sites the proxy opens, with their subdomains. ALLOWED_DOMAINS (comma-separated) replaces the
# list, and "*" allows any public site, for local testing.
DEFAULT_DOMAINS = [
    "reddit.com", "redd.it",
    # Finnish news
    "yle.fi", "hs.fi", "is.fi", "iltalehti.fi", "mtvuutiset.fi", "kauppalehti.fi", "talouselama.fi",
    "aamulehti.fi", "ts.fi", "kaleva.fi", "ksml.fi", "savonsanomat.fi", "satakunnankansa.fi",
    "uusisuomi.fi", "verkkouutiset.fi", "maaseuduntulevaisuus.fi", "hbl.fi", "vasabladet.fi",
    "suomenuutiset.fi", "apu.fi", "seura.fi",
]
_allowed = os.environ.get("ALLOWED_DOMAINS", "").strip()
ALLOWED = None if _allowed == "*" else [d.strip().lower() for d in _allowed.split(",") if d.strip()] or DEFAULT_DOMAINS


def allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return ALLOWED is None or any(host == d or host.endswith("." + d) for d in ALLOWED)

# Pages rendered at once; each is a Chromium tab.
RENDERS = asyncio.Semaphore(int(os.environ.get("MAX_RENDERS", "2")))

app = FastAPI(title="HashTrust proxy view")


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
        "HashTrust proxy view",
        f"<main><h1>HashTrust <span>proxy view</span></h1><p>Open an article and see what the "
        f"extension would flag in it and its comments.</p>{form()}"
        + (f"<p class=meta>Opens {html.escape(', '.join(ALLOWED))}.</p>" if ALLOWED else "")
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
<title>HashTrust proxy view</title>
<style>html,body{{margin:0;height:100%;overflow:hidden;background:#0f172a}}
iframe{{position:fixed;top:0;left:0;width:calc(100vw - 340px);height:100%;border:0;background:#fff}}
body.ht-collapsed iframe{{width:calc(100vw - 44px)}}
#ht-loading{{position:fixed;top:0;right:0;bottom:0;width:340px;box-sizing:border-box;padding:16px;
background:#0f172a;color:#94a3b8;font:14px/1.5 system-ui,sans-serif;border-left:1px solid #1e293b}}
#ht-loading b{{color:#e2e8f0;font-weight:650}}</style>
<iframe id=ht-frame src="{src}"></iframe>
<div id=ht-loading><b>HashTrust</b> proxy view<p>Rendering the page and loading its comments…</p></div>"""


@app.get("/view", response_class=HTMLResponse)
def view(url: str, test: bool = False):
    """The shell: the page in a same-origin frame left of the panel, so its layout gets the real width."""
    src = "frame?url=" + quote(url, safe="") + ("&test=1" if test else "")
    return SHELL.format(src=html.escape(src, quote=True))


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
async def frame(request: Request, url: str, test: bool = False):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if reason := await asyncio.to_thread(private_reason, url):
        return error(url, reason, 400)
    async with RENDERS:
        return await frame_page(request, url, test)


async def frame_page(request: Request, url: str, test: bool):
    result = None
    mode = "plain fetch"
    if RENDER:
        try:
            result = await render.render(url)
            mode = "rendered"
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
    }
    data = lxml.html.fragment_fromstring(
        "<script id=hashtrust-data type=application/json></script>", create_parent=False
    )
    # </script> inside the JSON would end the element early.
    data.text = json.dumps(config).replace("</", "<\\/")
    script = lxml.html.Element("script")
    script.text = (HERE / "overlay.js").read_text()
    body = doc.find("body")
    if body is None:
        body = doc
    body.append(data)
    body.append(script)
    return HTMLResponse("<!doctype html>\n" + lxml.html.tostring(doc, encoding="unicode"))


@app.post("/check")
async def check(request: Request):
    """Same-origin forward to the API's /check, so the page's CSP or CORS never gets in the way."""
    body = await request.body()
    async with httpx.AsyncClient(timeout=180) as client:
        try:
            r = await client.post(f"{API}/check", content=body, headers={"content-type": "application/json"})
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"API unreachable: {e}"}, status_code=502)
    return JSONResponse(r.json() if r.headers.get("content-type", "").startswith("application/json")
                        else {"error": r.text[:500]}, status_code=r.status_code)


if __name__ == "__main__":
    print(f"HashTrust proxy view on http://localhost:{PORT}  (API {API})")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
