import os

import pytest

from app import vectors
from app.jobs import paraphrase

AXIS = {"a": [1.0] + [0.0] * 383, "b": [0.0, 1.0] + [0.0] * 382}


@pytest.fixture
def embedder(monkeypatch):
    """Embeds a text starting with 'a' or 'b' to that axis; any other text gets no embedding."""

    def fingerprint(blocks, embed=False):
        return {"blocks": [{"embedding": AXIS.get(text[0])} for text in blocks]}

    monkeypatch.setattr(paraphrase.fpclient, "fingerprint", fingerprint)


def with_claim(conn, narrative_id):
    conn.execute("UPDATE narrative SET claim = 'The claim.' WHERE id = %s", (narrative_id,))
    conn.commit()
    return narrative_id


def test_paraphrases_ship_in_the_index_and_the_narrative_is_no_longer_pending(
    client, conn, narrative, embedder
):
    first = with_claim(conn, narrative(["s1"], content_hash="fp-1"))
    second = with_claim(conn, narrative(["s2"], content_hash="fp-2"))
    with_claim(conn, narrative(["s3"], state="candidate", content_hash="fp-3"))
    calls = []

    def fake(narratives):
        calls.append([n["id"] for n in narratives])
        return {first: ["a first", "x too short"], second: ["b second"]}

    assert paraphrase.pending(conn) == [first, second]
    added = paraphrase.add_paraphrases(conn, paraphrase.pending(conn), fake)
    conn.commit()

    assert calls == [[first, second]]
    assert added == 2
    assert paraphrase.pending(conn) == []
    entries = {e["narrativeId"]: e["vectors"] for e in client.get("/index").json()["narrativeVectors"]}
    assert entries == {str(first): [vectors.quantize(AXIS["a"])], str(second): [vectors.quantize(AXIS["b"])]}


def test_an_answer_without_a_narratives_list_is_rejected():
    with pytest.raises(TypeError, match="narratives"):
        paraphrase.parse_paraphrases({"paraphrases": []}, {1})


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY is not set")
def test_claude_paraphrases_a_claim_in_several_languages():
    result = paraphrase.claude_paraphrase(
        [
            {
                "id": 7,
                "title": "Sanctions hurt Europe more than Russia",
                "claim": "EU sanctions harm Europe, not Russia.",
            }
        ]
    )
    assert len(result[7]) >= 5
