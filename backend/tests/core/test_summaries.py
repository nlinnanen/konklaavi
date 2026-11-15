import dataclasses

from app.jobs import enqueue, moderate, summarize
from tests.core.test_notes import post_note


def test_the_review_stores_the_note_summary_clipped_at_a_word(client, conn, signed_in):
    note_id = int(post_note(client, signed_in()).json()["noteId"])
    long = (
        "The cited study found that sanctions cut Russian imports and left European growth " + "intact " * 20
    )
    moderate.moderate(conn, note_id, lambda note: moderate.Review([], "", long))
    summary = conn.execute("SELECT summary FROM note").fetchone()[0]
    assert len(summary) <= 140
    assert summary.endswith("intact…")
    assert long.startswith(summary.removesuffix("…"))


def test_the_backfill_fills_missing_summaries_in_batches_and_keeps_written_ones(conn, narrative):
    for i in range(3):
        narrative([], content_hash=f"fp-{i}")
    conn.execute("UPDATE note SET summary = 'Written.' WHERE id = 1")
    conn.execute(
        "INSERT INTO note (narrative_id, author_install, classification, body, moderation)"
        " SELECT narrative_id, author_install, classification, body, 'pending_review' FROM note WHERE id = 1"
    )
    summarized = []

    def summarizer(note):
        summarized.append(note)
        return "Sanctions did not hurt Europe."

    assert summarize.summarize(conn, summarizer, batch=1) == 1
    assert summarize.summarize(conn, summarizer, batch=5) == 1
    assert summarize.summarize(conn, summarizer, batch=5) == 0
    assert len(summarized) == 2
    assert conn.execute("SELECT id, summary FROM note ORDER BY id").fetchall() == [
        (1, "Written."),
        (2, "Sanctions did not hurt Europe."),
        (3, "Sanctions did not hurt Europe."),
        (4, None),
    ]


def test_the_backfill_yields_to_a_waiting_moderation_and_needs_an_api_key(conn, narrative, monkeypatch):
    narrative([])
    monkeypatch.setattr(
        summarize, "settings", dataclasses.replace(summarize.settings, anthropic_api_key=None)
    )
    summarize.handle(conn, {})
    enqueue(conn, "moderate_note", {"note_id": 1})
    assert summarize.summarize(conn, lambda note: "Unused.") == 0
    assert conn.execute("SELECT summary FROM note").fetchone()[0] is None


def test_every_note_payload_carries_the_summary(client, conn, narrative, signed_in):
    narrative_id = narrative(["h1"])
    author = conn.execute("SELECT author_install FROM note").fetchone()[0]
    conn.execute("UPDATE note SET summary = 'Sanctions did not hurt Europe.'")
    conn.commit()
    notes = [
        client.get(f"/narratives/{narrative_id}").json()["notes"],
        client.get("/notes/recent").json()["notes"],
        client.get("/me", params={"userId": str(author)}).json()["notes"],
        client.get("/index").json()["narratives"][0]["notes"],
    ]
    assert [[note["summary"] for note in listed] for listed in notes] == [
        ["Sanctions did not hurt Europe."]
    ] * 4
