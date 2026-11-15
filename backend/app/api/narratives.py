"""Narratives as the API ships them: display state, notes shown to readers, and spread.

A narrative's display state follows its notes shown to readers, which are its visible notes not rated
not helpful: 'misleading' when a misleading note is helpful, 'context' when a not-misleading note is
helpful, then 'controversial', 'needs_ratings' while none is decided, and 'tracked' without any.
"""

from collections.abc import Iterable

from fastapi import APIRouter, HTTPException
from psycopg import Connection
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.db import connection

router = APIRouter()

# Strongest first; a passage matching several narratives shows the strongest.
STATES = ("misleading", "controversial", "context", "needs_ratings", "tracked")
# Narratives born from a note, unless moderators hid every note on them. The offline benchmark's
# narratives have no notes and ship.
SHIPPED = (
    "n.source = 'note' AND ("
    " EXISTS (SELECT 1 FROM note x WHERE x.narrative_id = n.id AND x.moderation <> 'hidden')"
    " OR NOT EXISTS (SELECT 1 FROM note x WHERE x.narrative_id = n.id))"
)


class Body(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def note_rank(note: dict) -> tuple:
    """Sort key: the helpful misleading note first, then helpful context, controversial, undecided."""
    decided = {"helpful": 0, "controversial": 1, "needs_more_ratings": 2}[note["status"]]
    return decided, note["classification"] != "misleading", note["createdAt"]


def display_state(notes: list[dict]) -> str:
    helpful = {note["classification"] for note in notes if note["status"] == "helpful"}
    if "misleading" in helpful:
        return "misleading"
    if helpful:
        return "context"
    if any(note["status"] == "controversial" for note in notes):
        return "controversial"
    return "needs_ratings" if notes else "tracked"


def shown_notes(conn: Connection, narrative_ids: list[int]) -> dict[int, list[dict]]:
    rows = conn.execute(
        """
        SELECT n.narrative_id, n.id, n.classification, n.body, n.summary, n.sources, n.status, n.decided_by,
               n.created_at,
               count(r.*) FILTER (WHERE r.helpfulness = 'helpful'),
               count(r.*) FILTER (WHERE r.helpfulness = 'somewhat'),
               count(r.*) FILTER (WHERE r.helpfulness = 'not_helpful')
        FROM note n LEFT JOIN note_rating r ON r.note_id = n.id
        WHERE n.narrative_id = ANY(%s) AND n.moderation = 'visible' AND n.status <> 'not_helpful'
        GROUP BY n.id
        """,
        (narrative_ids,),
    ).fetchall()
    notes: dict[int, list[dict]] = {id_: [] for id_ in narrative_ids}
    for (
        narrative_id,
        id_,
        classification,
        body,
        summary,
        sources,
        status,
        decided_by,
        created,
        helpful,
        somewhat,
        unhelpful,
    ) in rows:
        notes[narrative_id].append(
            {
                "id": str(id_),
                "classification": classification,
                "body": body,
                "summary": summary,
                "sources": sources,
                "status": status,
                "decidedBy": decided_by,
                "ratings": {"helpful": helpful, "somewhat": somewhat, "not_helpful": unhelpful},
                "createdAt": created.isoformat(),
            }
        )
    for listed in notes.values():
        listed.sort(key=note_rank)
    return notes


def payloads(conn: Connection, narrative_ids: Iterable[int], pages: bool = False) -> dict[int, dict]:
    """The shipped form of each narrative: id, title, state, notes and spread.

    Spread counts the sites and, with `pages`, the pages its blocks were observed on; firstSeen is the
    first observation, or the narrative's creation before any.
    """
    ids = sorted(set(narrative_ids))
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT n.id, n.title, count(DISTINCT p.domain_id), count(DISTINCT o.page_id),
               coalesce(min(o.seen_at), n.created_at)
        FROM narrative n
        LEFT JOIN variant va ON va.narrative_id = n.id
        LEFT JOIN fingerprint f ON f.variant_id = va.id
        LEFT JOIN observation o ON o.fingerprint_id = f.id
        LEFT JOIN page p ON p.id = o.page_id
        WHERE n.id = ANY(%s)
        GROUP BY n.id
        """,
        (ids,),
    ).fetchall()
    notes = shown_notes(conn, ids)
    result = {}
    for id_, title, sites, page_count, first_seen in rows:
        spread = {"sites": sites, "firstSeen": first_seen.isoformat()}
        if pages:
            spread["pages"] = page_count
        result[id_] = {
            "narrativeId": str(id_),
            "title": title,
            "state": display_state(notes[id_]),
            "notes": notes[id_],
            "spread": spread,
        }
    return result


@router.get("/narratives/{narrative_id}")
def get_narrative(narrative_id: int) -> dict:
    with connection() as conn:
        shipped = conn.execute(
            f"SELECT 1 FROM narrative n WHERE n.id = %s AND {SHIPPED}", (narrative_id,)
        ).fetchone()
        if shipped is None:
            raise HTTPException(404, f"narrative {narrative_id} does not exist")
        return payloads(conn, [narrative_id], pages=True)[narrative_id]
