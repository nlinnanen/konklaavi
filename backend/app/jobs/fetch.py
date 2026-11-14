import logging
from dataclasses import dataclass
from datetime import date, timedelta
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urlsplit

import httpx
import numpy as np
import trafilatura
from psycopg import Connection

from app import fpclient
from app.jobs import enqueue, register
from app.jobs.variants import boilerplate, narrative_text, near_duplicates, update_centroids

# A common desktop browser string: some sites refuse unknown clients, and nothing here identifies a user.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 20.0
MAX_BYTES = 5 * 1024 * 1024
# Cosine between a block and the narrative's title and claim above which the block carries the narrative.
RELEVANCE_THRESHOLD = 0.5
# A hit refetches a page fetched longer ago than this.
HIT_REFETCH_AFTER = timedelta(hours=24)

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
        stored.append(Stored(fingerprint_id, new))
    return stored


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


def relevant(blocks: list[tuple[int, list[float]]], narrative_embedding: list[float]) -> list[int]:
    """The ids of the (id, embedding) blocks whose cosine to the narrative is at least
    RELEVANCE_THRESHOLD, and always the closest one."""
    if not blocks:
        return []
    scores = np.array([embedding for _, embedding in blocks]) @ np.array(narrative_embedding)
    return [id_ for (id_, _), score in zip(blocks, scores, strict=True) if score >= RELEVANCE_THRESHOLD] or [
        blocks[int(scores.argmax())][0]
    ]


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


def attach_relevant(
    conn: Connection, narrative_id: int, fingerprint_ids: list[int], narrative_embedding: list[float]
) -> None:
    """Attach the relevant blocks among the fingerprints that are not boilerplate to the narrative."""
    skip = boilerplate(conn, fingerprint_ids)
    blocks = conn.execute(
        "SELECT id, embedding::real[] FROM fingerprint WHERE id = ANY(%s) AND embedding IS NOT NULL",
        ([id_ for id_ in fingerprint_ids if id_ not in skip],),
    ).fetchall()
    attach_to_narrative(conn, narrative_id, relevant(blocks, narrative_embedding))


def repick(conn: Connection, narrative_ids: set[int]) -> None:
    """Attach relevant blocks of their fetched pages to those of the narratives that have none left."""
    empty = [
        row[0]
        for row in conn.execute(
            """
            SELECT n.id FROM narrative n
            WHERE n.id = ANY(%s) AND NOT EXISTS (
              SELECT 1 FROM variant v JOIN fingerprint f ON f.variant_id = v.id WHERE v.narrative_id = n.id
            )
            ORDER BY n.id
            """,
            (list(narrative_ids),),
        )
    ]
    if not empty:
        return
    texts = [narrative_text(conn, narrative_id) for narrative_id in empty]
    embedded = fpclient.fingerprint(texts, embed=True)["blocks"]
    for narrative_id, block in zip(empty, embedded, strict=True):
        if block["embedding"] is None:
            continue
        fingerprint_ids = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT o.fingerprint_id FROM observation o"
                " JOIN narrative_page np ON np.page_id = o.page_id WHERE np.narrative_id = %s",
                (narrative_id,),
            )
        ]
        attach_relevant(conn, narrative_id, fingerprint_ids, block["embedding"])


def fetched_recently(conn: Connection, url: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM page WHERE url = %s AND fetched_at > now() - %s", (url, HIT_REFETCH_AFTER)
        ).fetchone()
        is not None
    )


def ingest(
    conn: Connection, page_id: int, article: Article, narrative_id: int | None, text: str | None
) -> set[int]:
    """Store the article's blocks and attach the relevant ones; return the narratives whose blocks changed."""
    # The narrative's title and claim ride along as one extra block, embedded in the same call.
    extra = [text] if narrative_id is not None else []
    result = fpclient.fingerprint(article.blocks + extra, embed=True)["blocks"]
    stored = store_fingerprints(conn, page_id, article.blocks, result[: len(article.blocks)])
    if not stored:
        return set()
    ids = [s.id for s in stored]

    # Near-duplicates on other pages may have become boilerplate through this page.
    changed = detach(conn, boilerplate(conn, near_duplicates(conn, ids)))
    narrative_embedding = result[-1]["embedding"] if extra else None
    if narrative_embedding is not None:
        attach_relevant(conn, narrative_id, ids, narrative_embedding)
        changed.add(narrative_id)
    elif narrative_id is not None:
        log.warning("narrative %s has no sentence to embed; nothing attached", narrative_id)
    repick(conn, changed)
    update_centroids(conn, changed)

    new = [s.id for s in stored if s.new]
    if new:
        enqueue(conn, "match_variants", {"fingerprint_ids": new})
    return changed


@register("fetch")
def handle(conn: Connection, payload: dict) -> None:
    url = payload["url"]
    if payload.get("reason") == "hit" and fetched_recently(conn, url):
        return
    narrative_id = payload.get("narrative_id")
    text = narrative_text(conn, narrative_id) if narrative_id is not None else None
    if narrative_id is not None and text is None:
        log.warning("fetch %s: narrative %s does not exist", url, narrative_id)
        narrative_id = None

    status, html = download(url)
    article = extract(html) if html is not None else None
    if status == "ok" and article is None:
        status = "failed"
    page_id = upsert_page(conn, url, status, article)
    if narrative_id is not None:
        conn.execute(
            "INSERT INTO narrative_page (narrative_id, page_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (narrative_id, page_id),
        )
    changed = ingest(conn, page_id, article, narrative_id, text) if article is not None else set()
    # The payload's narrative gets its claim vector even when its page failed.
    if narrative_id is not None:
        changed.add(narrative_id)
    if changed:
        enqueue(conn, "narrative_vectors", {"narrative_ids": sorted(changed)})
