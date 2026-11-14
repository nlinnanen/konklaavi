import base64
import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

import httpx
import numpy as np
import trafilatura
from fastapi import APIRouter, HTTPException
from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app import fpclient
from app.config import settings
from app.db import connection
from app.jobs import enqueue
from app.jobs.fetch import domain_of
from app.trust import NEGATIVE_VERDICTS, SHIPPED_STATES, VERDICTS, Tally, tallies

router = APIRouter()

MAX_LOOKUP_HASHES = 500
MAX_HITS = 500
# LSH bands a block must share with a fingerprint to match it at the variant level.
MIN_SHARED_BANDS = 2
BANDS = 32
# Blocks shorter than this are too generic to match a narrative by meaning.
MIN_NARRATIVE_WORDS = 8
# A URL gets at most one hit fetch in this interval.
HIT_FETCH_INTERVAL = timedelta(days=1)

Verdict = Literal["misinformation", "manipulative", "misleading", "trustworthy"]


class Body(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class VoteRequest(Body):
    user_id: UUID
    content_hash: str = Field(min_length=1)
    sentence_hashes: list[str] = Field(min_length=1)
    verdict: Verdict
    comment: str | None = Field(default=None, max_length=500)
    url: str
    domain: str
    excerpt: str | None = None
    created_at: datetime


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


def summarize(hash: str, tally: Tally, signature: dict | None = None) -> dict:
    """A LookupResult. A signature from an active narrative flags the hash whatever the votes say.

    Counts are distinct users per verdict; flagging and the top verdict follow the weighted votes.
    """
    by_votes = tally.flags()
    by_narrative = signature is not None and signature["state"] == "active"
    if by_votes:
        top = tally.top_verdict(NEGATIVE_VERDICTS)
    elif by_narrative:
        top = signature["verdict"]
    else:
        top = tally.top_verdict()
    result = {
        "hash": hash,
        "counts": tally.users,
        "total": tally.total_users,
        "flagged": by_votes or by_narrative,
        "topVerdict": top,
    }
    if signature is not None:
        result["signature"] = signature
    return result


def vote_tallies(conn: Connection, hashes: list[str]) -> dict[str, Tally]:
    """Weighted votes per sentence hash, each user's latest vote on the hash."""
    rows = conn.execute(
        "SELECT DISTINCT ON (h, v.user_id) h, v.user_id, v.verdict FROM vote v, unnest(v.sentence_hashes) h"
        " WHERE v.sentence_hashes && %(hashes)s AND h = ANY(%(hashes)s)"
        " ORDER BY h, v.user_id, v.received_at DESC",
        {"hashes": hashes},
    ).fetchall()
    return tallies(conn, rows)


SIGNATURE_COLUMNS = "n.id, n.title, n.verdict, n.techniques, n.explanation, n.source, n.source_url, n.state"


def signature(row: tuple, match_level: str) -> dict:
    narrative_id, title, verdict, techniques, explanation, source, source_url, state = row
    return {
        "narrativeId": str(narrative_id),
        "title": title,
        "verdict": verdict,
        "techniques": techniques,
        "explanation": explanation,
        "source": source,
        "sourceUrl": source_url,
        "state": "active" if state == "active" else "under_review",
        "matchLevel": match_level,
    }


def exact_signatures(conn: Connection, hashes: list[str]) -> dict[str, dict]:
    """The signature of each sentence hash that belongs to an active or under-review narrative."""
    rows = conn.execute(
        f"""
        SELECT DISTINCT ON (s.hash) s.hash, {SIGNATURE_COLUMNS}
        FROM sentence s
        JOIN fingerprint f ON f.id = s.fingerprint_id
        JOIN variant va ON va.id = f.variant_id
        JOIN narrative n ON n.id = va.narrative_id
        WHERE s.hash = ANY(%s) AND n.state = ANY(%s)
        ORDER BY s.hash, n.state = 'active' DESC, n.updated_at DESC
        """,
        (hashes, list(SHIPPED_STATES)),
    ).fetchall()
    return {row[0]: signature(row[1:], "exact") for row in rows}


def variant_signatures(conn: Connection, blocks: list[BlockBands]) -> dict[str, dict]:
    """The signature of each block sharing MIN_SHARED_BANDS LSH bands with a narrative's fingerprint.

    Among several matching narratives an active one wins, then the one sharing the most bands.
    """
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
        SELECT DISTINCT ON (sh.block) sh.block, {SIGNATURE_COLUMNS}
        FROM shared sh
        JOIN fingerprint f ON f.id = sh.fingerprint_id
        JOIN variant va ON va.id = f.variant_id
        JOIN narrative n ON n.id = va.narrative_id
        WHERE n.state = ANY(%(states)s)
        ORDER BY sh.block, n.state = 'active' DESC, sh.bands DESC, n.updated_at DESC
        """,
        {
            "keys": keys,
            "bands": bands,
            "hashes": hashes,
            "min_shared": MIN_SHARED_BANDS,
            "states": list(SHIPPED_STATES),
        },
    ).fetchall()
    return {row[0]: signature(row[1:], "variant") for row in rows}


def lookup(conn: Connection, hashes: list[str], blocks: list[BlockBands]) -> dict[str, dict]:
    """LookupResults keyed by sentence or block hash, for the hashes that have votes or a signature.

    A hash that is both a sentence and a block hash keeps its exact signature.
    """
    hashes = list(dict.fromkeys(hashes))
    votes = vote_tallies(conn, hashes)
    signatures = {**variant_signatures(conn, blocks), **exact_signatures(conn, hashes)}
    keys = dict.fromkeys([*hashes, *(block.hash for block in blocks)])
    return {
        h: summarize(h, votes.get(h, Tally()), signatures.get(h))
        for h in keys
        if h in votes or h in signatures
    }


def narrative_signatures(conn: Connection, embeddings: dict[str, list[float]]) -> dict[str, dict]:
    """The signature of each block whose cosine to one of a shipped narrative's vectors reaches the threshold.

    Among several matching narratives an active one wins, then the most similar.
    """
    if not embeddings:
        return {}
    rows = conn.execute(
        f"""
        WITH q AS (
          SELECT * FROM unnest(%(keys)s::text[], %(embeddings)s::text[]) AS q(block, embedding)
        )
        SELECT DISTINCT ON (q.block) q.block, {SIGNATURE_COLUMNS}
        FROM q
        JOIN narrative_vector nv ON 1 - (nv.embedding <=> q.embedding::vector) >= %(threshold)s
        JOIN narrative n ON n.id = nv.narrative_id
        WHERE n.state = ANY(%(states)s)
        ORDER BY q.block, n.state = 'active' DESC, nv.embedding <=> q.embedding::vector, n.updated_at DESC
        """,
        {
            "keys": list(embeddings),
            "embeddings": [str(embedding) for embedding in embeddings.values()],
            "threshold": settings.narrative_match_threshold,
            "states": list(SHIPPED_STATES),
        },
    ).fetchall()
    return {row[0]: signature(row[1:], "narrative") for row in rows}


@router.post("/votes")
def post_vote(vote: VoteRequest) -> dict:
    with connection() as conn:
        vote_id = conn.execute(
            """
            INSERT INTO vote (user_id, content_hash, sentence_hashes, verdict, comment, url, domain, excerpt,
                              created_at)
            VALUES (%(user_id)s, %(content_hash)s, %(sentence_hashes)s, %(verdict)s, %(comment)s, %(url)s,
                    %(domain)s, %(excerpt)s, %(created_at)s)
            ON CONFLICT (user_id, content_hash) DO UPDATE SET
              sentence_hashes = EXCLUDED.sentence_hashes, verdict = EXCLUDED.verdict,
              comment = EXCLUDED.comment, url = EXCLUDED.url, domain = EXCLUDED.domain,
              excerpt = EXCLUDED.excerpt, created_at = EXCLUDED.created_at, received_at = now()
            RETURNING id
            """,
            vote.model_dump(),
        ).fetchone()[0]
        enqueue(conn, "fetch", {"url": vote.url, "reason": "vote", "vote_id": vote_id})
        enqueue(conn, "analyze", {"vote_id": vote_id})
    return {"ok": True}


@router.post("/lookup")
def post_lookup(request: LookupRequest) -> dict:
    with connection() as conn:
        results = lookup(conn, request.hashes, request.blocks)
    return {"results": list(results.values())}


@router.get("/domains/{domain}")
def get_domain(domain: str) -> dict:
    with connection() as conn:
        rows = conn.execute(
            "SELECT content_hash, user_id, verdict FROM vote WHERE domain = %s", (domain,)
        ).fetchall()
        per_content = tallies(conn, rows)
    overall = Tally()
    for tally in per_content.values():
        for verdict in VERDICTS:
            overall.users[verdict] += tally.users[verdict]
            overall.weight[verdict] += tally.weight[verdict]
    votes = overall.total_users
    return {
        "domain": domain,
        "votesCount": votes,
        "flaggedCount": sum(tally.flags() for tally in per_content.values()),
        "counts": overall.users,
        "topVerdict": (
            overall.top_verdict(NEGATIVE_VERDICTS if overall.mostly_negative() else VERDICTS)
            if votes
            else None
        ),
    }


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


@router.post("/check")
def post_check(request: CheckRequest) -> dict:
    """Matches per sentence, and per block for blocks that match only at the variant or narrative level."""
    if request.text:
        blocks = text_blocks(request.text)
    elif request.url:
        blocks = fetch_blocks(request.url)
    else:
        raise HTTPException(422, "give text or url")
    fingerprinted = fpclient.fingerprint(blocks, embed=True)["blocks"] if blocks else []
    hashes = [s["hash"] for block in fingerprinted for s in block["sentences"]]
    banded = [
        BlockBands(hash=block["contentHash"], bands=block["bands"])
        for block in fingerprinted
        if block["contentHash"] and block["bands"]
    ]
    with connection() as conn:
        results = lookup(conn, hashes, banded)
        unmatched = {
            block["contentHash"]: block["embedding"]
            for block in fingerprinted
            if block["embedding"] is not None
            and len(blocks[block["index"]].split()) >= MIN_NARRATIVE_WORDS
            and block["contentHash"] not in results
            and not any(s["hash"] in results for s in block["sentences"])
        }
        for hash, match in narrative_signatures(conn, unmatched).items():
            results[hash] = summarize(hash, Tally(), match)
    matches = []
    for block in fingerprinted:
        sentence_matches = [
            {"text": s["text"], "result": results[s["hash"]]}
            for s in block["sentences"]
            if s["hash"] in results
        ]
        matches += sentence_matches
        if not sentence_matches and block["contentHash"] in results:
            matches.append({"text": blocks[block["index"]], "result": results[block["contentHash"]]})
    return {"matches": matches}


def quantize(embedding: list[float]) -> str:
    """A unit vector as base64 of its int8 values round(v * 127)."""
    return base64.b64encode(np.rint(np.array(embedding) * 127).astype(np.int8).tobytes()).decode()


def index_signature(row: tuple) -> dict:
    result = signature(row, "exact")
    del result["matchLevel"]
    return result


@router.get("/index")
def get_index(since: int | None = None) -> dict:
    """The shipped signatures, in full or as the narratives changed since a version.

    The version is the oldest transaction still running at read time: every change from an older
    transaction is in the response, and a later one is sent again next time. A delta carries each
    changed narrative's sentences and fingerprints in full, and lists the ones no longer shipped.
    """
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        version = conn.execute("SELECT pg_snapshot_xmin(pg_current_snapshot())::text::bigint").fetchone()[0]
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
        params = {"states": list(SHIPPED_STATES), "full": full, "changed": changed}
        narratives = conn.execute(
            f"SELECT {SIGNATURE_COLUMNS} FROM narrative n"
            " WHERE n.state = ANY(%(states)s) AND (%(full)s OR n.id = ANY(%(changed)s)) ORDER BY n.id",
            params,
        ).fetchall()
        ids = [row[0] for row in narratives]
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
        "signatures": [index_signature(row) for row in narratives],
        "sentences": [{"hash": hash, "narrativeId": str(narrative_id)} for hash, narrative_id in sentences],
        "fingerprints": fingerprints,
        "narrativeThreshold": settings.narrative_match_threshold,
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
