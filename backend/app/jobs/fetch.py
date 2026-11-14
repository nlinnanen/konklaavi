import logging
from dataclasses import dataclass
from datetime import date
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urlsplit

import httpx
import trafilatura
from psycopg import Connection

from app import fpclient
from app.jobs import register

# A common desktop browser string: some sites refuse unknown clients, and nothing here identifies a user.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 20.0
MAX_BYTES = 5 * 1024 * 1024

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Article:
    title: str | None
    published_at: date | None
    blocks: list[str]


def domain_of(url: str) -> str:
    """The domain as the extension stores it: lowercase hostname without a leading www."""
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def download(url: str) -> tuple[str, bytes | None]:
    """Fetch a page without cookies or credentials. Returns (status, html), html only for a public page."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        # A jar that refuses every cookie, so a redirect chain cannot set one and send it back.
        no_cookies = CookieJar(DefaultCookiePolicy(allowed_domains=[]))
        with (
            httpx.Client(
                headers=headers, cookies=no_cookies, follow_redirects=True, timeout=TIMEOUT
            ) as client,
            client.stream("GET", url) as response,
        ):
            content_type = response.headers.get("content-type", "")
            if response.status_code != 200 or "html" not in content_type:
                return "private", None
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    log.warning("fetch %s: larger than %d bytes", url, MAX_BYTES)
                    return "failed", None
            return "ok", bytes(body)
    except httpx.HTTPError as error:
        log.warning("fetch %s: %s", url, error)
        return "failed", None


def extract(html: bytes) -> Article | None:
    """Main text, title and publish date; a block is one paragraph of the main text."""
    doc = trafilatura.bare_extraction(html, with_metadata=True, include_comments=False)
    if doc is None or not doc.text:
        return None
    blocks = [line.strip() for line in doc.text.split("\n") if line.strip()]
    published = None
    if doc.date:
        try:
            published = date.fromisoformat(doc.date)
        except ValueError:
            pass
    return Article(title=doc.title, published_at=published, blocks=blocks)


def upsert_page(conn: Connection, url: str, status: str, article: Article | None = None) -> int:
    domain_id = conn.execute(
        "INSERT INTO domain (name) VALUES (%s)"
        " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (domain_of(url),),
    ).fetchone()[0]
    return conn.execute(
        """
        INSERT INTO page (url, domain_id, title, published_at, fetched_at, status)
        VALUES (%s, %s, %s, %s, now(), %s)
        ON CONFLICT (url) DO UPDATE SET
          title = coalesce(EXCLUDED.title, page.title),
          published_at = coalesce(EXCLUDED.published_at, page.published_at),
          fetched_at = EXCLUDED.fetched_at,
          status = EXCLUDED.status
        RETURNING id
        """,
        (url, domain_id, article and article.title, article and article.published_at, status),
    ).fetchone()[0]


def store_fingerprints(conn: Connection, page_id: int, article: Article) -> list[int]:
    """Store each block that has a content hash, its sentences and an observation; return fingerprint ids."""
    result = fpclient.fingerprint(article.blocks)
    ids = []
    for block in result["blocks"]:
        if block["contentHash"] is None:
            continue
        # The no-op update makes RETURNING yield the existing row while first_seen and first_page_id stay.
        fingerprint_id = conn.execute(
            """
            INSERT INTO fingerprint (content_hash, content, first_page_id) VALUES (%s, %s, %s)
            ON CONFLICT (content_hash) DO UPDATE SET content_hash = EXCLUDED.content_hash
            RETURNING id
            """,
            (block["contentHash"], article.blocks[block["index"]], page_id),
        ).fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO sentence (hash, fingerprint_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [(s["hash"], fingerprint_id) for s in block["sentences"]],
            )
        conn.execute(
            "INSERT INTO observation (seen_at, fingerprint_id, page_id, source) VALUES (now(), %s, %s, 'fetch')",
            (fingerprint_id, page_id),
        )
        ids.append(fingerprint_id)
    return ids


def attach_to_narrative(conn: Connection, narrative_id: int, fingerprint_ids: list[int]) -> None:
    """Put the fingerprints that have no variant yet into one new variant of the narrative."""
    unassigned = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM fingerprint WHERE id = ANY(%s) AND variant_id IS NULL", (fingerprint_ids,)
        )
    ]
    if not unassigned:
        return
    variant_id = conn.execute(
        "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative_id,)
    ).fetchone()[0]
    conn.execute("UPDATE fingerprint SET variant_id = %s WHERE id = ANY(%s)", (variant_id, unassigned))


@register("fetch")
def handle(conn: Connection, payload: dict) -> None:
    url = payload["url"]
    status, html = download(url)
    article = extract(html) if html is not None else None
    if status == "ok" and article is None:
        status = "failed"
    page_id = upsert_page(conn, url, status, article)
    if article is None:
        return
    fingerprint_ids = store_fingerprints(conn, page_id, article)
    if payload.get("narrative_id") is not None and fingerprint_ids:
        attach_to_narrative(conn, payload["narrative_id"], fingerprint_ids)
