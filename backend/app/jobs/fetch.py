import logging
from dataclasses import dataclass
from datetime import date
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urlsplit

import httpx
import numpy as np
import trafilatura
from psycopg import Connection

from app import fpclient
from app.jobs import enqueue, register

# A common desktop browser string: some sites refuse unknown clients, and nothing here identifies a user.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 20.0
MAX_BYTES = 5 * 1024 * 1024
# Cosine between a block and the narrative's title and claim above which the block carries the narrative.
RELEVANCE_THRESHOLD = 0.5

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


@dataclass(frozen=True)
class Stored:
    """A page block stored as a fingerprint."""

    id: int
    new: bool
    embedding: list[float]


def store_fingerprints(
    conn: Connection, page_id: int, blocks: list[str], fingerprints: list[dict]
) -> list[Stored]:
    """Store each block that has a content hash, its sentences, bands and an observation."""
    stored = []
    for block in fingerprints:
        if block["contentHash"] is None:
            continue
        # The update keeps first_seen and first_page_id and fills a signature or embedding the row lacks.
        fingerprint_id, new = conn.execute(
            """
            INSERT INTO fingerprint (content_hash, content, minhash, embedding, first_page_id)
            VALUES (%s, %s, %s, %s::vector, %s)
            ON CONFLICT (content_hash) DO UPDATE SET
              minhash = coalesce(fingerprint.minhash, EXCLUDED.minhash),
              embedding = coalesce(fingerprint.embedding, EXCLUDED.embedding)
            RETURNING id, xmax = 0
            """,
            (
                block["contentHash"],
                blocks[block["index"]],
                block["minhash"],
                str(block["embedding"]),
                page_id,
            ),
        ).fetchone()
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO sentence (hash, fingerprint_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [(s["hash"], fingerprint_id) for s in block["sentences"]],
            )
            cur.executemany(
                "INSERT INTO lsh_band (band, hash, fingerprint_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                [(band, hash_, fingerprint_id) for band, hash_ in enumerate(block["bands"])],
            )
        conn.execute(
            "INSERT INTO observation (seen_at, fingerprint_id, page_id, source) VALUES (now(), %s, %s, 'fetch')",
            (fingerprint_id, page_id),
        )
        stored.append(Stored(fingerprint_id, new, block["embedding"]))
    return stored


def boilerplate(conn: Connection, page_id: int, fingerprint_ids: list[int]) -> set[int]:
    """The fingerprints seen on at least two pages of this page's domain."""
    rows = conn.execute(
        """
        SELECT o.fingerprint_id FROM observation o
        JOIN page p ON p.id = o.page_id
        WHERE o.fingerprint_id = ANY(%s)
          AND p.domain_id = (SELECT domain_id FROM page WHERE id = %s)
        GROUP BY o.fingerprint_id
        HAVING count(DISTINCT o.page_id) >= 2
        """,
        (fingerprint_ids, page_id),
    )
    return {row[0] for row in rows}


def detach(conn: Connection, fingerprint_ids: set[int]) -> set[int]:
    """Take the fingerprints out of their variants; return the narratives they left."""
    rows = conn.execute(
        """
        UPDATE fingerprint f SET variant_id = NULL
        FROM variant v
        WHERE v.id = f.variant_id AND f.id = ANY(%s)
        RETURNING v.narrative_id
        """,
        (list(fingerprint_ids),),
    )
    return {row[0] for row in rows if row[0] is not None}


def relevant(blocks: list[Stored], narrative_embedding: list[float]) -> list[int]:
    """The blocks whose cosine to the narrative is at least RELEVANCE_THRESHOLD, and always the closest one."""
    if not blocks:
        return []
    scores = np.array([b.embedding for b in blocks]) @ np.array(narrative_embedding)
    return [b.id for b, score in zip(blocks, scores, strict=True) if score >= RELEVANCE_THRESHOLD] or [
        blocks[int(scores.argmax())].id
    ]


def narrative_text(conn: Connection, narrative_id: int) -> str:
    title, claim = conn.execute(
        "SELECT title, claim FROM narrative WHERE id = %s", (narrative_id,)
    ).fetchone()
    return "\n".join(part for part in (title, claim) if part)


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


def update_centroids(conn: Connection, narrative_ids: set[int]) -> None:
    """Set each narrative's centroid to the mean embedding of its fingerprints."""
    conn.execute(
        """
        UPDATE narrative n SET centroid = (
          SELECT avg(f.embedding) FROM fingerprint f JOIN variant v ON v.id = f.variant_id
          WHERE v.narrative_id = n.id
        )
        WHERE n.id = ANY(%s)
        """,
        (list(narrative_ids),),
    )


@register("fetch")
def handle(conn: Connection, payload: dict) -> None:
    url = payload["url"]
    narrative_id = payload.get("narrative_id")
    status, html = download(url)
    article = extract(html) if html is not None else None
    if status == "ok" and article is None:
        status = "failed"
    page_id = upsert_page(conn, url, status, article)
    if article is None:
        return
    # The narrative's title and claim ride along as one extra block, embedded in the same call.
    extra = [narrative_text(conn, narrative_id)] if narrative_id is not None else []
    result = fpclient.fingerprint(article.blocks + extra, embed=True)["blocks"]
    stored = store_fingerprints(conn, page_id, article.blocks, result[: len(article.blocks)])
    if not stored:
        return

    repeated = boilerplate(conn, page_id, [s.id for s in stored])
    changed = detach(conn, repeated)
    narrative_embedding = result[-1]["embedding"] if extra else None
    if narrative_embedding is not None:
        attach_to_narrative(
            conn, narrative_id, relevant([s for s in stored if s.id not in repeated], narrative_embedding)
        )
        changed.add(narrative_id)
    elif narrative_id is not None:
        log.warning("narrative %s has no sentence to embed; nothing attached", narrative_id)
    update_centroids(conn, changed)

    new = [s.id for s in stored if s.new]
    if new:
        enqueue(conn, "match_variants", {"fingerprint_ids": new})
