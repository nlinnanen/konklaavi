from app import fpclient


def lookup(client, hashes):
    response = client.post("/lookup", json={"hashes": hashes})
    assert response.status_code == 200, response.text
    return {r["hash"]: r for r in response.json()["results"]}


def test_lookup_returns_only_hashes_with_votes_or_signature(client, vote):
    vote("misleading", ["h1"])
    assert set(lookup(client, ["h1", "unknown"])) == {"h1"}


def test_a_single_negative_vote_does_not_flag(client, vote):
    vote("misinformation", ["h1"])
    result = lookup(client, ["h1"])["h1"]
    assert (result["flagged"], result["total"], result["topVerdict"]) == (False, 1, "misinformation")


def test_half_negative_votes_flag_with_the_top_negative_verdict(client, vote):
    vote("manipulative", ["h1"])
    vote("trustworthy", ["h1"])
    result = lookup(client, ["h1"])["h1"]
    assert (result["flagged"], result["topVerdict"]) == (True, "manipulative")

    vote("trustworthy", ["h1"])
    result = lookup(client, ["h1"])["h1"]
    assert (result["flagged"], result["topVerdict"]) == (False, "trustworthy")


def test_votes_count_per_sentence_hash(client, vote):
    vote("misleading", ["h1", "h2"])
    vote("misleading", ["h2", "h3"])
    results = lookup(client, ["h1", "h2", "h3"])
    assert {h: r["counts"]["misleading"] for h, r in results.items()} == {"h1": 1, "h2": 2, "h3": 1}


def test_repeat_vote_replaces_the_earlier_one_and_enqueues_jobs(client, vote, conn):
    first = vote("misinformation", ["h1"])
    vote("trustworthy", ["h1"], content_hash=first["contentHash"], user=first["userId"])
    result = lookup(client, ["h1"])["h1"]
    assert (result["total"], result["counts"]["trustworthy"]) == (1, 1)
    vote_id = conn.execute("SELECT id FROM vote").fetchone()[0]
    jobs = conn.execute("SELECT kind, payload FROM job ORDER BY id").fetchall()
    assert jobs[-2:] == [
        ("fetch", {"url": "https://example.com/article", "reason": "vote", "vote_id": vote_id}),
        ("analyze", {"vote_id": vote_id}),
    ]


def test_narrative_hash_carries_a_signature_and_is_flagged_when_active(client, narrative, conn):
    narrative_id = narrative(["h1"], state="under_review", verdict="manipulative")
    result = lookup(client, ["h1"])["h1"]
    assert result["flagged"] is False
    assert result["signature"] == {
        "narrativeId": str(narrative_id),
        "title": "Sanctions destroy Europe",
        "verdict": "manipulative",
        "techniques": ["fear_appeal"],
        "explanation": "Why.",
        "source": "euvsdisinfo",
        "sourceUrl": "https://case",
        "state": "under_review",
        "matchLevel": "exact",
    }

    conn.execute("UPDATE narrative SET state = 'active'")
    conn.commit()
    result = lookup(client, ["h1"])["h1"]
    assert (result["flagged"], result["topVerdict"], result["total"]) == (True, "manipulative", 0)


def test_active_narrative_flags_despite_trustworthy_votes(client, narrative, vote):
    narrative(["h1"])
    vote("trustworthy", ["h1"])
    vote("trustworthy", ["h1"])
    result = lookup(client, ["h1"])["h1"]
    assert (result["flagged"], result["topVerdict"]) == (True, "misinformation")


def test_narratives_outside_review_or_active_give_no_signature(client, narrative):
    narrative(["h1"], state="candidate")
    assert lookup(client, ["h1"]) == {}


def test_domain_stats_count_votes_and_flagged_content(client, vote):
    vote("misinformation", ["a"], content_hash="x")
    vote("misleading", ["a"], content_hash="x")
    vote("trustworthy", ["b"], content_hash="y")
    vote("misinformation", ["c"], content_hash="z", domain="other.org")
    stats = client.get("/domains/example.com").json()
    assert stats == {
        "domain": "example.com",
        "votesCount": 3,
        "flaggedCount": 1,
        "counts": {"misinformation": 1, "misleading": 1, "manipulative": 0, "trustworthy": 1},
        "topVerdict": "misinformation",
    }


def test_domain_without_votes_has_no_top_verdict(client):
    stats = client.get("/domains/nowhere.net").json()
    assert (stats["votesCount"], stats["flaggedCount"], stats["topVerdict"]) == (0, 0, None)


def test_check_matches_text_blocks_against_narratives(client, narrative):
    known = "The western sanctions are destroying Europe from within, experts say."
    [block] = fpclient.fingerprint([known])["blocks"]
    narrative([s["hash"] for s in block["sentences"]])
    text = f"An unrelated opening paragraph with plenty of words.\n\nIntro words here. {known}\n"
    response = client.post("/check", json={"text": text})
    assert response.status_code == 200, response.text
    [match] = response.json()["matches"]
    assert match["text"] == known
    assert match["result"]["flagged"] is True


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
    assert (result["flagged"], result["total"], result["topVerdict"]) == (True, 0, "misinformation")
    assert (result["signature"]["narrativeId"], result["signature"]["matchLevel"]) == (
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


def test_block_hash_that_is_also_a_matched_sentence_keeps_the_exact_signature(client, narrative, conn):
    narrative(["h1"], content_hash="fp-1")
    add_bands(conn, "fp-1", ["b0", "b1"])
    response = client.post(
        "/lookup", json={"hashes": ["h1"], "blocks": [{"hash": "h1", "bands": ["b0", "b1"]}]}
    )
    [result] = response.json()["results"]
    assert result["signature"]["matchLevel"] == "exact"


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
    assert (match["text"], match["result"]["hash"], match["result"]["signature"]["matchLevel"]) == (
        edited,
        "edited",
        "variant",
    )
