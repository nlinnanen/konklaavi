import re
from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx
import trafilatura
from fastapi import APIRouter, HTTPException
from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app import fpclient
from app.db import connection
from app.jobs import enqueue

router = APIRouter()

VERDICTS = ("misinformation", "manipulative", "misleading", "trustworthy")
NEGATIVE_VERDICTS = VERDICTS[:3]
# Share of negative votes needed to flag content, as in client/lib/aggregate.ts.
FLAG_THRESHOLD = 0.5
# One vote is never enough to flag content on votes alone.
MIN_VOTES = 2
MAX_LOOKUP_HASHES = 500

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
    blocks: list[BlockBands] | None = None


class CheckRequest(Body):
    text: str | None = None
    url: str | None = None


def empty_counts() -> dict[str, int]:
    return dict.fromkeys(VERDICTS, 0)


def _argmax(counts: dict[str, int], among: tuple[str, ...]) -> str:
    return max(among, key=lambda v: counts[v])


def votes_flag(counts: dict[str, int], min_votes: int = MIN_VOTES) -> bool:
    total = sum(counts.values())
    negative = sum(counts[v] for v in NEGATIVE_VERDICTS)
    return total >= min_votes and total > 0 and negative / total >= FLAG_THRESHOLD


def summarize(hash: str, counts: dict[str, int], signature: dict | None = None) -> dict:
    """A LookupResult. A signature from an active narrative flags the hash whatever the votes say."""
    by_votes = votes_flag(counts)
    by_narrative = signature is not None and signature["state"] == "active"
    if by_votes:
        top = _argmax(counts, NEGATIVE_VERDICTS)
    elif by_narrative:
        top = signature["verdict"]
    else:
        top = _argmax(counts, VERDICTS)
    result = {
        "hash": hash,
        "counts": counts,
        "total": sum(counts.values()),
        "flagged": by_votes or by_narrative,
        "topVerdict": top,
    }
    if signature is not None:
        result["signature"] = signature
    return result


def vote_counts(conn: Connection, hashes: list[str]) -> dict[str, dict[str, int]]:
    rows = conn.execute(
        "SELECT h, v.verdict, count(*) FROM vote v, unnest(v.sentence_hashes) h"
        " WHERE v.sentence_hashes && %(hashes)s AND h = ANY(%(hashes)s) GROUP BY h, v.verdict",
        {"hashes": hashes},
    ).fetchall()
    counts: dict[str, dict[str, int]] = {}
    for hash, verdict, count in rows:
        counts.setdefault(hash, empty_counts())[verdict] = count
    return counts


def exact_signatures(conn: Connection, hashes: list[str]) -> dict[str, dict]:
    """The signature of each sentence hash that belongs to an active or under-review narrative."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (s.hash)
          s.hash, n.id, n.title, n.verdict, n.techniques, n.explanation, n.source, n.source_url, n.state
        FROM sentence s
        JOIN fingerprint f ON f.id = s.fingerprint_id
        JOIN variant va ON va.id = f.variant_id
        JOIN narrative n ON n.id = va.narrative_id
        WHERE s.hash = ANY(%s) AND n.state IN ('active', 'under_review')
        ORDER BY s.hash, n.state = 'active' DESC, n.updated_at DESC
        """,
        (hashes,),
    ).fetchall()
    return {
        hash: {
            "narrativeId": str(narrative_id),
            "title": title,
            "verdict": verdict,
            "techniques": techniques,
            "explanation": explanation,
            "source": source,
            "sourceUrl": source_url,
            "state": state,
            "matchLevel": "exact",
        }
        for hash, narrative_id, title, verdict, techniques, explanation, source, source_url, state in rows
    }


def lookup_hashes(conn: Connection, hashes: list[str]) -> dict[str, dict]:
    """LookupResults keyed by hash, for the hashes that have votes or a signature."""
    hashes = list(dict.fromkeys(hashes))
    counts = vote_counts(conn, hashes)
    signatures = exact_signatures(conn, hashes)
    return {
        h: summarize(h, counts.get(h, empty_counts()), signatures.get(h))
        for h in hashes
        if h in counts or h in signatures
    }


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
        results = lookup_hashes(conn, request.hashes)
    return {"results": list(results.values())}


@router.get("/domains/{domain}")
def get_domain(domain: str) -> dict:
    with connection() as conn:
        rows = conn.execute(
            "SELECT content_hash, verdict, count(*) FROM vote WHERE domain = %s GROUP BY 1, 2", (domain,)
        ).fetchall()
    counts = empty_counts()
    per_content: dict[str, dict[str, int]] = {}
    for content_hash, verdict, count in rows:
        counts[verdict] += count
        per_content.setdefault(content_hash, empty_counts())[verdict] = count
    votes = sum(counts.values())
    return {
        "domain": domain,
        "votesCount": votes,
        "flaggedCount": sum(votes_flag(c) for c in per_content.values()),
        "counts": counts,
        "topVerdict": (
            _argmax(counts, NEGATIVE_VERDICTS if votes_flag(counts, 1) else VERDICTS) if votes else None
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
    if request.text:
        blocks = text_blocks(request.text)
    elif request.url:
        blocks = fetch_blocks(request.url)
    else:
        raise HTTPException(422, "give text or url")
    sentences = (
        [s for block in fpclient.fingerprint(blocks)["blocks"] for s in block["sentences"]] if blocks else []
    )
    with connection() as conn:
        results = lookup_hashes(conn, [s["hash"] for s in sentences])
    return {
        "matches": [
            {"text": s["text"], "result": results[s["hash"]]} for s in sentences if s["hash"] in results
        ]
    }
