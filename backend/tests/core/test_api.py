import re

import pytest

from app import fpclient


def lookup(client, hashes):
    response = client.post("/lookup", json={"hashes": hashes})
    assert response.status_code == 200, response.text
    return {r["hash"]: r for r in response.json()["results"]}


def test_a_narrative_hash_carries_the_narrative_with_its_state_and_notes(client, narrative):
    narrative_id = narrative(["h1"], status="helpful")
    results = lookup(client, ["h1", "unknown"])
    assert set(results) == {"h1"}
    result = results["h1"]
    assert (result["matchLevel"], result["narrative"]["narrativeId"]) == ("exact", str(narrative_id))
    assert result["narrative"]["state"] == "misleading"
    assert [note["body"] for note in result["narrative"]["notes"]] == ["Context."]


@pytest.mark.parametrize(
    ("status", "classification", "moderation", "state"),
    [
        ("helpful", "not_misleading", "visible", "context"),
        ("controversial", "misleading", "visible", "controversial"),
        ("needs_more_ratings", "misleading", "visible", "needs_ratings"),
        ("not_helpful", "misleading", "visible", "tracked"),
        ("helpful", "misleading", "pending_review", "tracked"),
    ],
)
def test_the_display_state_follows_the_notes_shown(
    client, narrative, status, classification, moderation, state
):
    narrative(["h1"], status=status, classification=classification, moderation=moderation)
    assert lookup(client, ["h1"])["h1"]["narrative"]["state"] == state


def test_a_narrative_whose_notes_are_all_hidden_is_not_shipped(client, narrative):
    narrative(["h1"], moderation="hidden")
    assert lookup(client, ["h1"]) == {}


def test_check_matches_text_blocks_against_narratives(client, narrative):
    known = "The western sanctions are destroying Europe from within, experts say."
    [block] = fpclient.fingerprint([known])["blocks"]
    narrative([s["hash"] for s in block["sentences"]])
    text = f"An unrelated opening paragraph with plenty of words.\n\nIntro words here. {known}\n"
    response = client.post("/check", json={"text": text})
    assert response.status_code == 200, response.text
    [match] = response.json()["matches"]
    assert (match["text"], match["result"]["matchLevel"]) == (known, "exact")


def add_bands(conn, content_hash, bands):
    """Store LSH bands for the fingerprint with this content hash; bands[i] is band i."""
    conn.execute(
        "INSERT INTO lsh_band (band, hash, fingerprint_id)"
        " SELECT b.band - 1, b.hash, f.id FROM fingerprint f, unnest(%s::text[]) WITH ORDINALITY b(hash, band)"
        " WHERE f.content_hash = %s",
        (bands, content_hash),
    )
    conn.commit()


def lookup_blocks(client, blocks):
    response = client.post("/lookup", json={"hashes": [], "blocks": blocks})
    assert response.status_code == 200, response.text
    return {r["hash"]: r for r in response.json()["results"]}


def test_block_sharing_two_bands_with_a_narrative_matches_at_the_variant_level(client, narrative, conn):
    narrative_id = narrative([], content_hash="fp-1")
    add_bands(conn, "fp-1", ["b0", "b1", "b2", "b3"])
    result = lookup_blocks(client, [{"hash": "edited", "bands": ["b0", "x1", "b2", "x3"]}])["edited"]
    assert (result["narrative"]["narrativeId"], result["matchLevel"]) == (
        str(narrative_id),
        "variant",
    )


def test_block_sharing_one_band_does_not_match(client, narrative, conn):
    narrative([], content_hash="fp-1")
    add_bands(conn, "fp-1", ["b0", "b1", "b2", "b3"])
    assert lookup_blocks(client, [{"hash": "other", "bands": ["b0", "x1", "x2", "x3"]}]) == {}


def test_bands_match_only_at_the_same_band_index(client, narrative, conn):
    narrative([], content_hash="fp-1")
    add_bands(conn, "fp-1", ["b0", "b1", "b2", "b3"])
    assert lookup_blocks(client, [{"hash": "shifted", "bands": ["b1", "b0", "b3", "b2"]}]) == {}


def test_block_hash_that_is_also_a_matched_sentence_keeps_the_exact_match(client, narrative, conn):
    narrative(["h1"], content_hash="fp-1")
    add_bands(conn, "fp-1", ["b0", "b1"])
    response = client.post(
        "/lookup", json={"hashes": ["h1"], "blocks": [{"hash": "h1", "bands": ["b0", "b1"]}]}
    )
    [result] = response.json()["results"]
    assert result["matchLevel"] == "exact"


def test_check_reports_a_block_matching_only_at_the_variant_level(client, narrative, conn, monkeypatch):
    narrative([], content_hash="fp-1")
    add_bands(conn, "fp-1", ["b0", "b1"])
    edited = "An edited copy of the narrative paragraph with a few words changed."
    monkeypatch.setattr(
        fpclient,
        "fingerprint",
        lambda blocks, embed=False: {
            "version": "v1",
            "blocks": [
                {
                    "index": 0,
                    "contentHash": "edited",
                    "sentences": [{"text": edited, "start": 0, "end": len(edited), "hash": "s-edited"}],
                    "minhash": None,
                    "bands": ["b0", "b1"],
                    "embedding": None,
                }
            ],
        },
    )
    response = client.post("/check", json={"text": edited})
    assert response.status_code == 200, response.text
    [match] = response.json()["matches"]
    assert (match["text"], match["result"]["hash"], match["result"]["matchLevel"]) == (
        edited,
        "edited",
        "variant",
    )


def unit_vector(cosine):
    """A unit vector at this cosine to the first axis."""
    return [cosine, (1 - cosine**2) ** 0.5] + [0.0] * 382


def fake_fingerprint(embeddings):
    """A fingerprint service splitting sentences at periods and embedding a text as `embeddings` says."""

    def fingerprint(blocks, embed=False):
        result = []
        for i, block in enumerate(blocks):
            sentences = [
                {"text": m.group(), "start": m.start(), "end": m.end(), "hash": f"h:{m.group()}"}
                for m in re.finditer(r"[^.\s][^.]*\.", block)
            ]
            result.append(
                {
                    "index": i,
                    "contentHash": f"h:{block}",
                    "sentences": sentences,
                    "minhash": None,
                    "bands": None,
                    "embedding": embeddings.get(block, unit_vector(0.0)) if embed else None,
                }
            )
        return {"version": "v1", "blocks": result}

    return fingerprint


def test_check_matches_sentences_close_in_meaning_to_a_narrative(client, narrative, conn, monkeypatch):
    narrative_id = narrative([], content_hash="fp-1")
    conn.execute(
        "INSERT INTO narrative_vector (narrative_id, kind, embedding) VALUES (%s, 'claim', %s::vector)",
        (narrative_id, str(unit_vector(1.0))),
    )
    conn.commit()
    reworded = "Europe is being ruined from the inside by the sanctions of the West."
    short = "Sanctions are hurting European households."
    monkeypatch.setattr(
        fpclient, "fingerprint", fake_fingerprint({reworded: unit_vector(0.9), short: unit_vector(0.75)})
    )
    text = f"{reworded}\n\n{short} The weather in Lisbon was mild this week."
    response = client.post("/check", json={"text": text})
    assert response.status_code == 200, response.text
    matches = [
        (
            m["text"],
            m["result"]["narrative"]["narrativeId"],
            m["result"]["matchLevel"],
            m["result"]["similarity"],
        )
        for m in response.json()["matches"]
    ]
    assert matches == [
        (reworded, str(narrative_id), "narrative", 0.9),
        (short, str(narrative_id), "related", 0.75),
    ]
