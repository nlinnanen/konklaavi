import re
from datetime import datetime, timedelta

import httpx
import trafilatura
from fastapi import APIRouter, HTTPException
from psycopg import Connection
from pydantic import Field

from app import fpclient
from app.api import narratives, notes
from app.api.narratives import SHIPPED, STATES, Body, payloads
from app.config import settings
from app.db import connection
from app.jobs import enqueue
from app.jobs.fetch import domain_of
from app.vectors import quantize

router = APIRouter()
router.include_router(narratives.router)
router.include_router(notes.router)

MAX_LOOKUP_HASHES = 500
MAX_HITS = 500
# LSH bands a block must share with a fingerprint to match it at the variant level.
MIN_SHARED_BANDS = 2
BANDS = 32
# Consecutive sentences embedded together as one unit, besides each sentence alone.
RUN_SENTENCES = 3
# A URL gets at most one hit fetch in this interval.
HIT_FETCH_INTERVAL = timedelta(days=1)


class BlockBands(Body):
    hash: str
    bands: list[str]


class LookupRequest(Body):
    hashes: list[str] = Field(max_length=MAX_LOOKUP_HASHES)
    blocks: list[BlockBands] = Field(default=[], max_length=MAX_LOOKUP_HASHES)


class Hit(Body):
    hash: str = Field(min_length=1)
    narrative_id: int
    url: str = Field(pattern=r"^https?://")
    seen_at: datetime


class HitsRequest(Body):
    hits: list[Hit] = Field(max_length=MAX_HITS)


class CheckRequest(Body):
    text: str | None = None
    url: str | None = None


LEVELS = ("exact", "variant", "narrative", "related")
# Matches per key: (narrative id, match level, closeness), a higher closeness being a closer match.
Candidates = dict[str, list[tuple[int, str, float]]]


def exact_candidates(conn: Connection, hashes: list[str]) -> Candidates:
    """The shipped narratives holding each sentence hash."""
    rows = conn.execute(
        f"""
        SELECT DISTINCT s.hash, n.id FROM sentence s
        JOIN fingerprint f ON f.id = s.fingerprint_id
        JOIN variant va ON va.id = f.variant_id
        JOIN narrative n ON n.id = va.narrative_id
        WHERE s.hash = ANY(%s) AND {SHIPPED}
        """,
        (hashes,),
    ).fetchall()
    result: Candidates = {}
    for hash, narrative_id in rows:
        result.setdefault(hash, []).append((narrative_id, "exact", 0.0))
    return result


def variant_candidates(conn: Connection, blocks: list[BlockBands]) -> Candidates:
    """The shipped narratives with a fingerprint sharing MIN_SHARED_BANDS LSH bands with each block,
    closeness being the most bands shared."""
    if not blocks:
        return {}
    keys, bands, hashes = [], [], []
    for block in blocks:
        for band, hash in enumerate(block.bands):
            keys.append(block.hash)
            bands.append(band)
            hashes.append(hash)
    rows = conn.execute(
        f"""
        WITH q AS (
          SELECT * FROM unnest(%(keys)s::text[], %(bands)s::smallint[], %(hashes)s::text[]) AS q(block, band, hash)
        ), shared AS (
          SELECT q.block, l.fingerprint_id, count(*) AS bands
          FROM q JOIN lsh_band l ON l.band = q.band AND l.hash = q.hash
          GROUP BY q.block, l.fingerprint_id
          HAVING count(*) >= %(min_shared)s
        )
        SELECT sh.block, n.id, max(sh.bands)
        FROM shared sh
        JOIN fingerprint f ON f.id = sh.fingerprint_id
        JOIN variant va ON va.id = f.variant_id
        JOIN narrative n ON n.id = va.narrative_id
        WHERE {SHIPPED}
        GROUP BY 1, 2
        """,
        {"keys": keys, "bands": bands, "hashes": hashes, "min_shared": MIN_SHARED_BANDS},
    ).fetchall()
    result: Candidates = {}
    for block, narrative_id, shared in rows:
        result.setdefault(block, []).append((narrative_id, "variant", float(shared)))
    return result


def unit_candidates(conn: Connection, embeddings: dict[str, list[float]]) -> Candidates:
    """The shipped narratives with a vector at relatedThreshold or closer to each unit, closeness being
    the highest cosine: at narrativeThreshold the unit matches at the narrative level, below it is related."""
    if not embeddings:
        return {}
    rows = conn.execute(
        f"""
        WITH q AS (
          SELECT * FROM unnest(%(keys)s::text[], %(embeddings)s::text[]) AS q(unit, embedding)
        )
        SELECT q.unit, n.id, max(1 - (nv.embedding <=> q.embedding::vector))
        FROM q
        JOIN narrative_vector nv ON 1 - (nv.embedding <=> q.embedding::vector) >= %(related)s
        JOIN narrative n ON n.id = nv.narrative_id
        WHERE {SHIPPED}
        GROUP BY 1, 2
        """,
        {
            "keys": list(embeddings),
            "embeddings": [str(embedding) for embedding in embeddings.values()],
            "related": settings.related_match_threshold,
        },
    ).fetchall()
    result: Candidates = {}
    for unit, narrative_id, similarity in rows:
        level = "narrative" if similarity >= settings.narrative_match_threshold else "related"
        result.setdefault(unit, []).append((narrative_id, level, similarity))
    return result


def results(conn: Connection, *found: Candidates) -> dict[str, dict]:
    """A LookupResult per key holding its strongest match: the strongest level, then the strongest
    narrative state, then the closest. A narrative or related match carries its cosine as `similarity`."""
    merged: Candidates = {}
    for candidates in found:
        for key, matches in candidates.items():
            merged.setdefault(key, []).extend(matches)
    shipped = payloads(conn, {id_ for matches in merged.values() for id_, _, _ in matches})
    out = {}
    for key, matches in merged.items():
        narrative_id, level, closeness = min(
            matches,
            key=lambda m: (LEVELS.index(m[1]), STATES.index(shipped[m[0]]["state"]), -m[2], -m[0]),
        )
        out[key] = {"hash": key, "matchLevel": level, "narrative": shipped[narrative_id]}
        if level in ("narrative", "related"):
            out[key]["similarity"] = round(closeness, 4)
    return out


def lookup(conn: Connection, hashes: list[str], blocks: list[BlockBands]) -> dict[str, dict]:
    """LookupResults keyed by sentence or block hash, for the hashes that match a shipped narrative.

    A hash that is both a sentence and a block hash keeps its exact match.
    """
    hashes = list(dict.fromkeys(hashes))
    return results(conn, exact_candidates(conn, hashes), variant_candidates(conn, blocks))


@router.post("/lookup")
def post_lookup(request: LookupRequest) -> dict:
    with connection() as conn:
        found = lookup(conn, request.hashes, request.blocks)
    return {"results": list(found.values())}


def text_blocks(text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def fetch_blocks(url: str) -> list[str]:
    """The main text of a public page, one block per extracted paragraph."""
    try:
        response = httpx.get(url, follow_redirects=True, timeout=20)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(502, f"could not fetch {url}: {error}") from error
    text = trafilatura.extract(response.text) or ""
    return [line.strip() for line in text.splitlines() if line.strip()]


def units(sentences: list[dict]) -> list[str]:
    """The text of a block's embedding units: each sentence, and each run of RUN_SENTENCES sentences,
    their texts joined by a space as embeddingUnits in client/lib/embed.ts joins them."""
    runs = [sentences[i : i + RUN_SENTENCES] for i in range(len(sentences) - RUN_SENTENCES + 1)]
    return [" ".join(s["text"] for s in unit) for unit in [[s] for s in sentences] + runs]


@router.post("/check")
def post_check(request: CheckRequest) -> dict:
    """Matches per sentence, per block for blocks matching only at the variant level, and per embedding
    unit at the narrative or related level for blocks with neither."""
    if request.text:
        blocks = text_blocks(request.text)
    elif request.url:
        blocks = fetch_blocks(request.url)
    else:
        raise HTTPException(422, "give text or url")
    fingerprinted = fpclient.fingerprint(blocks)["blocks"] if blocks else []
    hashes = [s["hash"] for block in fingerprinted for s in block["sentences"]]
    banded = [
        BlockBands(hash=block["contentHash"], bands=block["bands"])
        for block in fingerprinted
        if block["contentHash"] and block["bands"]
    ]
    with connection() as conn:
        found = lookup(conn, hashes, banded)
    unmatched = {
        block["index"]: units(block["sentences"])
        for block in fingerprinted
        if block["contentHash"] not in found and not any(s["hash"] in found for s in block["sentences"])
    }
    texts = [text for block_units in unmatched.values() for text in block_units]
    embedded = fpclient.fingerprint(texts, embed=True)["blocks"] if texts else []
    unit_hashes = {texts[unit["index"]]: unit["contentHash"] for unit in embedded}
    with connection() as conn:
        close = unit_candidates(
            conn,
            {unit["contentHash"]: unit["embedding"] for unit in embedded if unit["embedding"] is not None},
        )
        unit_results = results(conn, close)
    matches = []
    for block in fingerprinted:
        sentence_matches = [
            {"text": s["text"], "result": found[s["hash"]]} for s in block["sentences"] if s["hash"] in found
        ]
        matches += sentence_matches
        if not sentence_matches and block["contentHash"] in found:
            matches.append({"text": blocks[block["index"]], "result": found[block["contentHash"]]})
        for text in unmatched.get(block["index"], []):
            if unit_hashes.get(text) in unit_results:
                matches.append({"text": text, "result": unit_results[unit_hashes[text]]})
    return {"matches": matches}


@router.get("/index")
def get_index(since: int | None = None) -> dict:
    """The shipped narratives, in full or as the narratives changed since a version.

    The version is the oldest transaction still running at read time: every change from an older
    transaction is in the response, and a later one is sent again next time. Transaction ids are
    cluster-wide, so transactions seen running in another database are left out; they never write
    index_change here and would otherwise hold the version back. A delta carries each changed
    narrative's sentences and fingerprints in full, and lists the ones no longer shipped.
    """
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        version = conn.execute(
            "SELECT coalesce(("
            "  SELECT min(x) FROM pg_snapshot_xip(s) x WHERE NOT EXISTS ("
            "   SELECT FROM pg_stat_activity a WHERE a.backend_xid = xid(x) AND a.datname <> current_database())"
            " ), pg_snapshot_xmax(s))::text::bigint FROM pg_current_snapshot() s"
        ).fetchone()[0]
        full = since is None or since > version
        changed = None
        if not full:
            changed = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT narrative_id FROM index_change WHERE xid >= %s::text::xid8",
                    (str(since),),
                )
            ]
        ids = [
            row[0]
            for row in conn.execute(
                f"SELECT n.id FROM narrative n WHERE {SHIPPED} AND (%s OR n.id = ANY(%s)) ORDER BY n.id",
                (full, changed),
            )
        ]
        sentences = conn.execute(
            "SELECT DISTINCT s.hash, va.narrative_id FROM sentence s"
            " JOIN fingerprint f ON f.id = s.fingerprint_id JOIN variant va ON va.id = f.variant_id"
            " WHERE va.narrative_id = ANY(%s) ORDER BY 2, 1",
            (ids,),
        ).fetchall()
        bands = conn.execute(
            "SELECT va.narrative_id, f.id, array_agg(l.band), array_agg(l.hash) FROM lsh_band l"
            " JOIN fingerprint f ON f.id = l.fingerprint_id JOIN variant va ON va.id = f.variant_id"
            " WHERE va.narrative_id = ANY(%s) GROUP BY 1, 2 ORDER BY 1, 2",
            (ids,),
        ).fetchall()
        vectors = conn.execute(
            "SELECT narrative_id, embedding::real[] FROM narrative_vector WHERE narrative_id = ANY(%s)"
            " ORDER BY narrative_id, kind = 'claim' DESC, fingerprint_id",
            (ids,),
        ).fetchall()
        shipped = payloads(conn, ids)
    narrative_vectors: dict[int, list[str]] = {id: [] for id in ids}
    for narrative_id, embedding in vectors:
        narrative_vectors[narrative_id].append(quantize(embedding))
    fingerprints = []
    for narrative_id, _, numbers, hashes in bands:
        slots: list[str | None] = [None] * BANDS
        for number, hash in zip(numbers, hashes, strict=True):
            slots[number] = hash
        fingerprints.append({"narrativeId": str(narrative_id), "bands": slots})
    return {
        "version": version,
        "full": full,
        "narratives": [shipped[id_] for id_ in ids],
        "sentences": [{"hash": hash, "narrativeId": str(narrative_id)} for hash, narrative_id in sentences],
        "fingerprints": fingerprints,
        "narrativeThreshold": settings.narrative_match_threshold,
        "relatedThreshold": settings.related_match_threshold,
        "narrativeVectors": [
            {"narrativeId": str(id), "vectors": encoded} for id, encoded in narrative_vectors.items()
        ],
        "removed": [] if full else [str(id) for id in sorted(set(changed) - set(ids))],
    }


def record_hit(conn: Connection, hit: Hit) -> None:
    """A detection observation of the hit's fingerprint, when the narrative holds the hash."""
    domain_id = conn.execute(
        "INSERT INTO domain (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (domain_of(hit.url),),
    ).fetchone()[0]
    page_id = conn.execute(
        "INSERT INTO page (url, domain_id) VALUES (%s, %s)"
        " ON CONFLICT (url) DO UPDATE SET url = EXCLUDED.url RETURNING id",
        (hit.url, domain_id),
    ).fetchone()[0]
    fingerprint = conn.execute(
        """
        SELECT f.id FROM fingerprint f JOIN variant va ON va.id = f.variant_id
        WHERE va.narrative_id = %(narrative)s
          AND (f.content_hash = %(hash)s OR f.id IN (SELECT fingerprint_id FROM sentence WHERE hash = %(hash)s))
        ORDER BY f.id LIMIT 1
        """,
        {"narrative": hit.narrative_id, "hash": hit.hash},
    ).fetchone()
    if fingerprint is not None:
        conn.execute(
            "INSERT INTO observation (seen_at, fingerprint_id, page_id, source)"
            " VALUES (least(%s, now()), %s, %s, 'detection')",
            (hit.seen_at, fingerprint[0], page_id),
        )


def enqueue_hit_fetch(conn: Connection, url: str, narrative_id: int) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (url,))
    recent = conn.execute(
        "SELECT 1 FROM job WHERE kind = 'fetch' AND payload->>'url' = %s AND payload->>'reason' = 'hit'"
        " AND created_at > now() - %s",
        (url, HIT_FETCH_INTERVAL),
    ).fetchone()
    if recent is None:
        enqueue(conn, "fetch", {"url": url, "reason": "hit", "narrative_id": narrative_id})


@router.post("/hits")
def post_hits(request: HitsRequest) -> dict:
    """Record detections by the extension and fetch each page once a day; hits on unknown narratives are dropped."""
    with connection() as conn:
        known = {
            row[0]
            for row in conn.execute(
                "SELECT id FROM narrative WHERE id = ANY(%s)", ([hit.narrative_id for hit in request.hits],)
            )
        }
        fetches: dict[str, int] = {}
        for hit in request.hits:
            if hit.narrative_id in known:
                record_hit(conn, hit)
                fetches.setdefault(hit.url, hit.narrative_id)
        for url in sorted(fetches):
            enqueue_hit_fetch(conn, url, fetches[url])
    return {"ok": True}
