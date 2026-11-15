"""Context notes: writing, rating, reporting and moderating them.

Any install writes and rates notes; reporting one needs an install signed in to an account. Scoring
counts each account once per note and each install without an account on its own (app/jobs/score.py).
"""

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from psycopg import Connection
from pydantic import AfterValidator, Field

from app import fpclient, trust
from app.api.narratives import Body
from app.db import connection
from app.jobs import enqueue, enqueue_unless_queued
from app.jobs.fetch import canonical_page_url, own_page

router = APIRouter()

# Two notes on new overlapping passages must not both create a narrative.
_CREATE_LOCK_KEY = 0x6E6F7465
MAX_SOURCES = 10
TITLE_LENGTH = 200

Url = Annotated[str, Field(max_length=2000, pattern=r"^https?://\S+$")]
# A page a reader is on, under its original URL when Konklaavi's demo proxy shows it.
PageUrl = Annotated[Url, AfterValidator(canonical_page_url)]


class Passage(Body):
    text: str = Field(min_length=1, max_length=5000)
    url: PageUrl
    # The server computes both when either is missing, as the extension would.
    content_hash: str | None = Field(default=None, min_length=1)
    sentence_hashes: list[str] | None = Field(default=None, min_length=1, max_length=200)


class NoteRequest(Body):
    user_id: UUID
    passage: Passage
    classification: Literal["misleading", "not_misleading"]
    body: str = Field(min_length=1, max_length=1000)
    sources: list[Url] = Field(default=[], max_length=MAX_SOURCES)


class RatingRequest(Body):
    user_id: UUID
    helpfulness: Literal["helpful", "somewhat", "not_helpful"]


class ReportRequest(Body):
    user_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class ModerationRequest(Body):
    user_id: UUID
    action: Literal["approve", "hide"]


def verified_account(conn: Connection, user_id: UUID) -> UUID:
    """The account the install is linked to; 403 for an install without one."""
    row = conn.execute("SELECT account_id FROM install WHERE id = %s", (user_id,)).fetchone()
    if row is None or row[0] is None:
        raise HTTPException(403, "sign in first")
    return row[0]


def install_account(conn: Connection, user_id: UUID) -> UUID | None:
    """The account the install is linked to, recording an install not seen before."""
    conn.execute("INSERT INTO install (id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    return conn.execute("SELECT account_id FROM install WHERE id = %s", (user_id,)).fetchone()[0]


def first_sentence(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", " ".join(text.split()), maxsplit=1)[0]
    return sentence if len(sentence) <= TITLE_LENGTH else sentence[: TITLE_LENGTH - 1].rstrip() + "…"


def hashed(passage: Passage) -> Passage:
    """The passage with its hashes, computed by the fingerprint service when the writer sent none.

    The text becomes its sentences of 5+ words joined by a space, the text the extension sends for a
    selection, and the hashes are those `fingerprintSentences` gives them; 422 when there is no such sentence.
    """
    if passage.content_hash and passage.sentence_hashes:
        return passage
    [block] = fpclient.fingerprint([passage.text])["blocks"]
    if block["contentHash"] is None:
        raise HTTPException(422, "the passage needs a sentence of 5 or more words")
    sentences = block["sentences"]
    return passage.model_copy(
        update={
            "text": " ".join(s["text"] for s in sentences),
            "content_hash": block["contentHash"],
            "sentence_hashes": list(dict.fromkeys(s["hash"] for s in sentences))[:200],
        }
    )


def note_narrative(conn: Connection, passage: Passage) -> int | None:
    """The note narrative holding one of the passage's sentence hashes or its content hash."""
    row = conn.execute(
        """
        SELECT n.id FROM fingerprint f
        JOIN variant va ON va.id = f.variant_id
        JOIN narrative n ON n.id = va.narrative_id
        WHERE n.source = 'note'
          AND (f.content_hash = %(content)s OR f.id IN (SELECT fingerprint_id FROM sentence WHERE hash = ANY(%(hashes)s)))
        ORDER BY n.id LIMIT 1
        """,
        {"content": passage.content_hash, "hashes": passage.sentence_hashes},
    ).fetchone()
    return row[0] if row else None


def create_narrative(conn: Connection, passage: Passage) -> int:
    """A narrative for the passage, with the passage as its fingerprint and claim.

    A passage already stored as a fingerprint of another narrative moves to the new one: a note is the
    reader's explicit word on that text, and only note narratives ship.
    """
    narrative_id = conn.execute(
        "INSERT INTO narrative (title, claim, source, source_url, state) VALUES (%s, %s, 'note', %s, 'active')"
        " RETURNING id",
        (first_sentence(passage.text), passage.text, passage.url),
    ).fetchone()[0]
    variant_id = conn.execute(
        "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative_id,)
    ).fetchone()[0]
    fingerprint_id = conn.execute(
        """
        INSERT INTO fingerprint (content_hash, content, variant_id) VALUES (%s, %s, %s)
        ON CONFLICT (content_hash) DO UPDATE SET variant_id = EXCLUDED.variant_id
        RETURNING id
        """,
        (passage.content_hash, passage.text, variant_id),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sentence (hash, fingerprint_id) SELECT unnest(%s::text[]), %s ON CONFLICT DO NOTHING",
        (passage.sentence_hashes, fingerprint_id),
    )
    if not own_page(passage.url):
        enqueue(conn, "fetch", {"url": passage.url, "reason": "note", "narrative_id": narrative_id})
    enqueue(conn, "narrative_vectors", {"narrative_ids": [narrative_id]})
    return narrative_id


@router.post("/notes")
def post_note(request: NoteRequest) -> dict:
    """Add a note to the narrative the passage belongs to, creating it from the passage when there is none.

    The note waits in pending_review until moderate_note reviews it.
    """
    passage = hashed(request.passage)
    with connection() as conn:
        account_id = install_account(conn, request.user_id)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_CREATE_LOCK_KEY,))
        narrative_id = note_narrative(conn, passage) or create_narrative(conn, passage)
        note_id = conn.execute(
            "INSERT INTO note (narrative_id, author_install, account_id, classification, body, sources)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                narrative_id,
                request.user_id,
                account_id,
                request.classification,
                request.body,
                request.sources,
            ),
        ).fetchone()[0]
        conn.execute("UPDATE narrative SET updated_at = now() WHERE id = %s", (narrative_id,))
        enqueue(conn, "moderate_note", {"note_id": note_id})
    return {"noteId": str(note_id), "narrativeId": str(narrative_id)}


@router.get("/notes/recent")
def get_recent(
    limit: Annotated[int, Query(ge=1, le=200)] = 50, since: Annotated[datetime | None, Query()] = None
) -> dict:
    """Visible notes, newest first, with the narrative and passage they are on: what raters pick from."""
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT n.id, n.narrative_id, r.title, r.claim, r.source_url, n.classification, n.body, n.summary,
                   n.sources, n.status, n.decided_by, n.created_at
            FROM note n JOIN narrative r ON r.id = n.narrative_id
            WHERE n.moderation = 'visible' AND (%(since)s::timestamptz IS NULL OR n.created_at > %(since)s)
            ORDER BY n.created_at DESC, n.id DESC
            LIMIT %(limit)s
            """,
            {"since": since, "limit": limit},
        ).fetchall()
    return {
        "notes": [
            {
                "id": str(id_),
                "narrativeId": str(narrative_id),
                "title": title,
                "passage": claim,
                "url": url,
                "classification": classification,
                "body": body,
                "summary": summary,
                "sources": sources,
                "status": status,
                "decidedBy": decided_by,
                "createdAt": created.isoformat(),
            }
            for (
                id_,
                narrative_id,
                title,
                claim,
                url,
                classification,
                body,
                summary,
                sources,
                status,
                decided_by,
                created,
            ) in rows
        ]
    }


def note_author(conn: Connection, note_id: int) -> tuple[UUID, UUID | None]:
    """The note's author install and account."""
    row = conn.execute("SELECT author_install, account_id FROM note WHERE id = %s", (note_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"note {note_id} does not exist")
    return row


@router.post("/notes/{note_id}/ratings")
def post_rating(note_id: int, request: RatingRequest) -> dict:
    """Store the install's rating of a note, replacing its earlier one. Authors don't rate their own notes.

    A new or changed rating queues a score_notes run.
    """
    with connection() as conn:
        author_install, author_account = note_author(conn, note_id)
        account_id = install_account(conn, request.user_id)
        if request.user_id == author_install or (account_id is not None and account_id == author_account):
            raise HTTPException(403, "you wrote this note")
        changed = conn.execute(
            """
            INSERT INTO note_rating (note_id, install, account_id, helpfulness) VALUES (%s, %s, %s, %s)
            ON CONFLICT (note_id, install) DO UPDATE SET
              account_id = EXCLUDED.account_id, helpfulness = EXCLUDED.helpfulness, created_at = now()
            WHERE note_rating.helpfulness <> EXCLUDED.helpfulness
            """,
            (note_id, request.user_id, account_id, request.helpfulness),
        ).rowcount
        if changed:
            enqueue_unless_queued(conn, "score_notes")
    return {"ok": True}


@router.post("/notes/{note_id}/report")
def post_report(note_id: int, request: ReportRequest) -> dict:
    """Send a visible note back to review with the reporter's reason, and have moderate_note review it."""
    with connection() as conn:
        verified_account(conn, request.user_id)
        note_author(conn, note_id)
        updated = conn.execute(
            "UPDATE note SET moderation = 'pending_review', moderation_reason = %s"
            " WHERE id = %s AND moderation = 'visible'",
            (f"reported: {request.reason}", note_id),
        ).rowcount
        if updated:
            enqueue(conn, "moderate_note", {"note_id": note_id})
            enqueue_unless_queued(conn, "score_notes")
    return {"ok": True}


def require_moderator(conn: Connection, user_id: UUID) -> None:
    if not trust.standing(conn, user_id).moderator:
        raise HTTPException(403, "moderators only")


@router.get("/moderation/queue")
def get_queue(user_id: Annotated[UUID, Query(alias="userId")]) -> dict:
    """The notes awaiting review, oldest first, with the passage they are on."""
    with connection() as conn:
        require_moderator(conn, user_id)
        rows = conn.execute(
            """
            SELECT n.id, n.narrative_id, r.title, r.claim, r.source_url, n.classification, n.body, n.summary,
                   n.sources, n.moderation_reason, n.created_at
            FROM note n JOIN narrative r ON r.id = n.narrative_id
            WHERE n.moderation = 'pending_review'
            ORDER BY n.created_at
            """
        ).fetchall()
    return {
        "notes": [
            {
                "id": str(id_),
                "narrativeId": str(narrative_id),
                "title": title,
                "passage": claim,
                "url": url,
                "classification": classification,
                "body": body,
                "summary": summary,
                "sources": sources,
                "reason": reason,
                "createdAt": created.isoformat(),
            }
            for id_, narrative_id, title, claim, url, classification, body, summary, sources, reason, created in rows
        ]
    }


@router.post("/moderation/notes/{note_id}")
def post_moderation(note_id: int, request: ModerationRequest) -> dict:
    """Approve a note (visible) or hide it, whatever its moderation state."""
    with connection() as conn:
        require_moderator(conn, request.user_id)
        moderate(conn, note_id, request.action)
    return {"ok": True}


def moderate(conn: Connection, note_id: int, action: Literal["approve", "hide"]) -> None:
    """Make the note visible or hide it, and have scoring rerun; 404 for an unknown note."""
    note_author(conn, note_id)
    if action == "approve":
        conn.execute(
            "UPDATE note SET moderation = 'visible', moderation_reason = NULL WHERE id = %s", (note_id,)
        )
    else:
        conn.execute("UPDATE note SET moderation = 'hidden' WHERE id = %s", (note_id,))
    enqueue_unless_queued(conn, "score_notes")
