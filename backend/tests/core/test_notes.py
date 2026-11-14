import dataclasses
import uuid

from app import trust
from app.jobs import moderate

PASSAGE = {
    "text": "Sanctions are destroying Europe from within. Experts agree it is over.",
    "url": "https://news.example/a",
    "contentHash": "c-passage",
    "sentenceHashes": ["p1", "p2"],
}


def post_note(client, user, passage=PASSAGE, **fields):
    body = {
        "userId": str(user),
        "passage": passage,
        "classification": "misleading",
        "body": "The cited study says the opposite.",
        "sources": ["https://study.example/1"],
        **fields,
    }
    return client.post("/notes", json=body)


def test_writing_a_note_needs_a_verified_account(client, conn):
    assert post_note(client, uuid.uuid4()).status_code == 403
    assert conn.execute("SELECT count(*) FROM narrative").fetchone()[0] == 0


def test_a_note_creates_a_narrative_whose_passage_looks_up_at_once(client, conn, signed_in):
    response = post_note(client, signed_in())
    assert response.status_code == 200, response.text
    narrative_id = response.json()["narrativeId"]

    [result] = client.post("/lookup", json={"hashes": ["p2", "other"]}).json()["results"]
    assert (result["hash"], result["matchLevel"]) == ("p2", "exact")
    narrative = result["narrative"]
    assert (narrative["narrativeId"], narrative["title"]) == (
        narrative_id,
        "Sanctions are destroying Europe from within.",
    )
    # The note waits for review, so the narrative is only tracked.
    assert (narrative["state"], narrative["notes"]) == ("tracked", [])

    jobs = conn.execute("SELECT kind, payload FROM job ORDER BY id").fetchall()
    assert jobs == [
        ("fetch", {"url": PASSAGE["url"], "reason": "note", "narrative_id": int(narrative_id)}),
        ("narrative_vectors", {"narrative_ids": [int(narrative_id)]}),
        ("moderate_note", {"note_id": int(response.json()["noteId"])}),
    ]


def test_a_note_on_a_passage_sharing_a_sentence_joins_its_narrative(client, signed_in):
    first = post_note(client, signed_in()).json()
    overlapping = {**PASSAGE, "contentHash": "c-other", "sentenceHashes": ["p2", "p3"]}
    second = post_note(client, signed_in(), overlapping, classification="not_misleading").json()
    assert second["narrativeId"] == first["narrativeId"]
    assert second["noteId"] != first["noteId"]


def test_moderation_keeps_the_note_pending_without_an_api_key(client, conn, signed_in, monkeypatch):
    note_id = int(post_note(client, signed_in()).json()["noteId"])
    monkeypatch.setattr(moderate, "settings", dataclasses.replace(moderate.settings, anthropic_api_key=None))
    moderate.handle(conn, {"note_id": note_id})
    assert conn.execute("SELECT moderation FROM note").fetchone()[0] == "pending_review"


def test_a_clean_review_shows_the_note_and_a_flagged_one_waits_with_its_reason(client, conn, signed_in):
    note_id = int(post_note(client, signed_in()).json()["noteId"])
    moderate.moderate(conn, note_id, lambda note: (["spam"], "Advertises a product."))
    assert conn.execute("SELECT moderation, moderation_reason FROM note").fetchone() == (
        "pending_review",
        "spam: Advertises a product.",
    )
    moderate.moderate(conn, note_id, lambda note: ([], ""))
    assert conn.execute("SELECT moderation, moderation_reason FROM note").fetchone() == ("visible", None)


def test_a_moderator_approves_a_queued_note_and_others_are_refused(client, signed_in, monkeypatch):
    moderator = signed_in("mod@example.org")
    monkeypatch.setattr(
        trust,
        "settings",
        dataclasses.replace(trust.settings, moderator_emails=frozenset({"mod@example.org"})),
    )
    note_id = post_note(client, signed_in()).json()["noteId"]
    assert client.get("/moderation/queue", params={"userId": str(signed_in())}).status_code == 403
    queue = client.get("/moderation/queue", params={"userId": str(moderator)}).json()["notes"]
    assert [note["id"] for note in queue] == [note_id]

    response = client.post(
        f"/moderation/notes/{note_id}", json={"userId": str(moderator), "action": "approve"}
    )
    assert response.status_code == 200
    [result] = client.post("/lookup", json={"hashes": ["p1"]}).json()["results"]
    assert (result["narrative"]["state"], len(result["narrative"]["notes"])) == ("needs_ratings", 1)
