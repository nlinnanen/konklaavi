import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import parse_qs, urlsplit

import httpx
import trafilatura
from psycopg import Connection

from app import fpclient
from app.config import settings
from app.jobs import enqueue, register
from app.jobs.variants import (
    MIN_JACCARD,
    MIN_SHARED_BANDS,
    NEAR_DUPLICATES,
    boilerplate,
    narrative_passage,
    near_duplicates,
    update_centroids,
)

# A common desktop browser string: some sites refuse unknown clients, and nothing here identifies a user.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 20.0
MAX_BYTES = 5 * 1024 * 1024
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


# Pages of the demo proxy (demo/proxy-view), such as /demo/view or /frame, carry the page they show in ?url=.
PROXY_PATH = re.compile(r".*/(view|frame)")


def canonical_page_url(url: str, own_hosts: frozenset[str] | None = None) -> str:
    """The page a URL shows: the original target of a page the demo proxy on one of the own hosts renders,
    the URL unchanged otherwise. canonicalPageUrl in client/lib/domain.ts has the same rule."""
    own_hosts = settings.own_hosts if own_hosts is None else own_hosts
    parts = urlsplit(url)
    target = parse_qs(parts.query).get("url", [""])[0].strip()
    if (parts.hostname or "").lower() not in own_hosts or not PROXY_PATH.fullmatch(parts.path) or not target:
        return url
    # The proxy opens a URL without a scheme over https.
    target = target if re.match(r"https?://", target, re.IGNORECASE) else "https://" + target
    return target if urlsplit(target).hostname else url


def own_page(url: str) -> bool:
    """Whether the URL is on one of HashTrust's own hosts, which are never tracked."""
    return (urlsplit(url).hostname or "").lower() in settings.own_hosts


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


def matching(
    conn: Connection, narrative_id: int, fingerprint_ids: list[int], passage_embedding: list[float] | None
) -> set[int]:
    """The fingerprints that carry the narrative, at the levels the extension matches on.

    Exact: a sentence shared with one of the narrative's fingerprints. Variant: a near-duplicate of one.
    Embedding: within NARRATIVE_MATCH_THRESHOLD cosine of the note passage or of a narrative vector.
    """
    params = {
        "ids": fingerprint_ids,
        "narrative": narrative_id,
        "passage": None if passage_embedding is None else str(passage_embedding),
        "distance": 1 - settings.narrative_match_threshold,
        "bands": MIN_SHARED_BANDS,
        "jaccard": MIN_JACCARD,
    }
    rows = conn.execute(
        f"""
        WITH near AS ({NEAR_DUPLICATES})
        SELECT near.id FROM near
        JOIN fingerprint d ON d.id = near.dup JOIN variant v ON v.id = d.variant_id
        WHERE v.narrative_id = %(narrative)s
        UNION
        SELECT mine.fingerprint_id FROM sentence mine
        JOIN sentence theirs ON theirs.hash = mine.hash
        JOIN fingerprint t ON t.id = theirs.fingerprint_id JOIN variant v ON v.id = t.variant_id
        WHERE mine.fingerprint_id = ANY(%(ids)s) AND v.narrative_id = %(narrative)s
        UNION
        SELECT f.id FROM fingerprint f
        WHERE f.id = ANY(%(ids)s) AND (
          f.embedding <=> %(passage)s::vector <= %(distance)s
          OR EXISTS (
            SELECT 1 FROM narrative_vector nv
            WHERE nv.narrative_id = %(narrative)s AND nv.embedding <=> f.embedding <= %(distance)s
          )
        )
        """,
        params,
    )
    return {row[0] for row in rows}


def fill_signature(conn: Connection, block: dict) -> None:
    """Give the stored fingerprint with the block's content hash the block's signature, embedding and
    bands when it lacks them, as a passage stored with a note does."""
    if block["contentHash"] is None:
        return
    row = conn.execute(
        """
        UPDATE fingerprint SET
          minhash = coalesce(minhash, %s),
          embedding = coalesce(embedding, %s::vector)
        WHERE content_hash = %s AND (minhash IS NULL OR embedding IS NULL)
        RETURNING id
        """,
        (
            block["minhash"],
            None if block["embedding"] is None else str(block["embedding"]),
            block["contentHash"],
        ),
    ).fetchone()
    if row is None:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO lsh_band (band, hash, fingerprint_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [(band, hash_, row[0]) for band, hash_ in enumerate(block["bands"])],
        )


def fetched_recently(conn: Connection, url: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM page WHERE url = %s AND fetched_at > now() - %s", (url, HIT_REFETCH_AFTER)
        ).fetchone()
        is not None
    )


def ingest(
    conn: Connection, page_id: int, article: Article, narrative_id: int | None, passage: str | None
) -> set[int]:
    """Store the article's blocks and attach those that carry the narrative; return the narratives whose
    blocks changed."""
    # The note passage rides along as one extra block, fingerprinted in the same call.
    extra = [passage] if passage else []
    result = fpclient.fingerprint(article.blocks + extra, embed=True)["blocks"]
    stored = store_fingerprints(conn, page_id, article.blocks, result[: len(article.blocks)])
    passage_block = result[-1] if extra else None
    if passage_block is not None:
        fill_signature(conn, passage_block)
    if not stored:
        return set()
    ids = [s.id for s in stored]

    # Near-duplicates on other pages may have become boilerplate through this page.
    changed = detach(conn, boilerplate(conn, near_duplicates(conn, ids)))
    if narrative_id is not None:
        skip = boilerplate(conn, ids)
        candidates = [id_ for id_ in ids if id_ not in skip]
        embedding = passage_block and passage_block["embedding"]
        attach_to_narrative(conn, narrative_id, sorted(matching(conn, narrative_id, candidates, embedding)))
        changed.add(narrative_id)
    update_centroids(conn, changed)

    new = [s.id for s in stored if s.new]
    if new:
        enqueue(conn, "match_variants", {"fingerprint_ids": new})
    return changed


@register("fetch")
def handle(conn: Connection, payload: dict) -> None:
    """Fetch a public page: a note's page (reason 'note'), a page an extension user matched (reason 'hit'),
    or any other page; with a narrative_id, attach the page's blocks that carry that narrative."""
    url = canonical_page_url(payload["url"])
    if own_page(url):
        return
    if payload.get("reason") == "hit" and fetched_recently(conn, url):
        return
    narrative_id = payload.get("narrative_id")
    passage = narrative_passage(conn, narrative_id) if narrative_id is not None else None
    if narrative_id is not None and passage is None:
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
    if article is not None:
        changed = ingest(conn, page_id, article, narrative_id, passage)
    else:
        changed = set()
        if passage:
            fill_signature(conn, fpclient.fingerprint([passage], embed=True)["blocks"][0])
    # The payload's narrative gets its claim vector even when its page failed.
    if narrative_id is not None:
        changed.add(narrative_id)
    if changed:
        enqueue(conn, "narrative_vectors", {"narrative_ids": sorted(changed)})
