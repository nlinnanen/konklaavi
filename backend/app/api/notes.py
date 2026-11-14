"""Context notes: writing, rating, reporting and moderating them.

Writing and reporting a note need an install linked to a verified account. Ratings are stored for any
install; scoring counts only those of installs linked to an account (app/jobs/score.py).
"""

import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from psycopg import Connection
from pydantic import Field

from app import trust
from app.api.narratives import Body
from app.db import connection
from app.jobs import enqueue

router = APIRouter()

# Two notes on new overlapping passages must not both create a narrative.
_CREATE_LOCK_KEY = 0x6E6F7465
MAX_SOURCES = 10
TITLE_LENGTH = 200

Url = Annotated[str, Field(max_length=2000, pattern=r"^https?://\S+$")]


class Passage(Body):
    text: str = Field(min_length=1, max_length=5000)
    url: Url
    content_hash: str = Field(min_length=1)
    sentence_hashes: list[str] = Field(min_length=1, max_length=200)


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
        raise HTTPException(403, "sign in with your email first")
    return row[0]


def first_sentence(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", " ".join(text.split()), maxsplit=1)[0]
    return sentence if len(sentence) <= TITLE_LENGTH else sentence[: TITLE_LENGTH - 1].rstrip() + "…"


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
    enqueue(conn, "fetch", {"url": passage.url, "reason": "note", "narrative_id": narrative_id})
    enqueue(conn, "narrative_vectors", {"narrative_ids": [narrative_id]})
    return narrative_id


@router.post("/notes")
def post_note(request: NoteRequest) -> dict:
    """Add a note to the narrative the passage belongs to, creating it from the passage when there is none.

    The note waits in pending_review until moderate_note reviews it.
    """
    with connection() as conn:
        account_id = verified_account(conn, request.user_id)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_CREATE_LOCK_KEY,))
        narrative_id = note_narrative(conn, request.passage) or create_narrative(conn, request.passage)
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


def note_author(conn: Connection, note_id: int) -> UUID:
    row = conn.execute("SELECT account_id FROM note WHERE id = %s", (note_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"note {note_id} does not exist")
    return row[0]


@router.post("/notes/{note_id}/ratings")
def post_rating(note_id: int, request: RatingRequest) -> dict:
    """Store the install's rating of a note, replacing its earlier one. Authors don't rate their own notes."""
    with connection() as conn:
        author = note_author(conn, note_id)
        conn.execute("INSERT INTO install (id) VALUES (%s) ON CONFLICT DO NOTHING", (request.user_id,))
        account_id = conn.execute(
            "SELECT account_id FROM install WHERE id = %s", (request.user_id,)
        ).fetchone()[0]
        if account_id is not None and account_id == author:
            raise HTTPException(403, "you wrote this note")
        conn.execute(
            """
            INSERT INTO note_rating (note_id, install, account_id, helpfulness) VALUES (%s, %s, %s, %s)
            ON CONFLICT (note_id, install) DO UPDATE SET
              account_id = EXCLUDED.account_id, helpfulness = EXCLUDED.helpfulness, created_at = now()
            WHERE note_rating.helpfulness <> EXCLUDED.helpfulness
            """,
            (note_id, request.user_id, account_id, request.helpfulness),
        )
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
            SELECT n.id, n.narrative_id, r.title, r.claim, r.source_url, n.classification, n.body, n.sources,
                   n.moderation_reason, n.created_at
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
                "sources": sources,
                "reason": reason,
                "createdAt": created.isoformat(),
            }
            for id_, narrative_id, title, claim, url, classification, body, sources, reason, created in rows
        ]
    }


@router.post("/moderation/notes/{note_id}")
def post_moderation(note_id: int, request: ModerationRequest) -> dict:
    """Approve a note (visible) or hide it, whatever its moderation state."""
    with connection() as conn:
        require_moderator(conn, request.user_id)
        note_author(conn, note_id)
        if request.action == "approve":
            conn.execute(
                "UPDATE note SET moderation = 'visible', moderation_reason = NULL WHERE id = %s", (note_id,)
            )
        else:
            conn.execute("UPDATE note SET moderation = 'hidden' WHERE id = %s", (note_id,))
    return {"ok": True}
