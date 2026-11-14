import dataclasses
import os
from types import SimpleNamespace

import pytest

from app.jobs import analyze

ANALYSIS = {
    "claim": "Sanctions hurt Europe more than Russia.",
    "title": "Sanctions destroy Europe",
    "techniques": ["fear_appeal"],
    "explanation": "It presents a prediction as fact.",
    "verdict": "misleading",
}


class FakeAnalyzer:
    def __init__(self):
        self.calls = []

    def __call__(self, excerpt, verdict, comment, url):
        self.calls.append(excerpt)
        return ANALYSIS


def vote_id(conn, content_hash):
    conn.commit()
    return conn.execute("SELECT id FROM vote WHERE content_hash = %s", (content_hash,)).fetchone()[0]


def narrative_of_vote(conn, id):
    conn.commit()
    return conn.execute("SELECT narrative_id FROM vote WHERE id = %s", (id,)).fetchone()[0]


def test_report_creates_a_narrative_under_review_that_matches_its_text(conn, client, vote):
    body = vote("misleading", ["s1", "s2"], content_hash="c1", excerpt="Sanctions destroy Europe.")
    fake = FakeAnalyzer()
    analyze.analyze_vote(conn, vote_id(conn, "c1"), fake)

    assert fake.calls == ["Sanctions destroy Europe."]
    narrative_id = narrative_of_vote(conn, vote_id(conn, "c1"))
    assert narrative_id is not None
    signature = client.post("/lookup", json={"hashes": ["s2"]}).json()["results"][0]["signature"]
    assert signature == {
        "narrativeId": str(narrative_id),
        "title": "Sanctions destroy Europe",
        "verdict": "misleading",
        "techniques": ["fear_appeal"],
        "explanation": "It presents a prediction as fact.",
        "source": "report",
        "sourceUrl": body["url"],
        "state": "under_review",
        "settled": False,
        "matchLevel": "exact",
        "reports": {"count": 1, "weight": 0.25},
    }


def test_report_on_known_sentences_extends_their_narrative_without_analysis(conn, vote, narrative):
    narrative_id = narrative(["s1"])
    vote("misinformation", ["s0", "s1"], content_hash="c2")
    fake = FakeAnalyzer()
    analyze.analyze_vote(conn, vote_id(conn, "c2"), fake)

    assert fake.calls == []
    assert narrative_of_vote(conn, vote_id(conn, "c2")) == narrative_id
    assert conn.execute("SELECT count(*) FROM narrative").fetchone()[0] == 1


def test_second_report_on_the_same_text_joins_the_first_narrative(conn, vote):
    vote("misleading", ["s1"], content_hash="c1")
    vote("manipulative", ["s1"], content_hash="c1")
    fake = FakeAnalyzer()
    ids = [row[0] for row in conn.execute("SELECT id FROM vote ORDER BY id")]
    for id in ids:
        analyze.analyze_vote(conn, id, fake)

    assert len(fake.calls) == 1
    assert narrative_of_vote(conn, ids[0]) == narrative_of_vote(conn, ids[1])


def test_report_on_a_fetched_block_puts_that_fingerprint_in_the_narrative(conn, vote):
    conn.execute("INSERT INTO fingerprint (content_hash, content) VALUES ('c1', 'fetched block')")
    vote("misleading", ["s1"], content_hash="c1")
    analyze.analyze_vote(conn, vote_id(conn, "c1"), FakeAnalyzer())

    narrative_id = narrative_of_vote(conn, vote_id(conn, "c1"))
    rows = conn.execute(
        "SELECT f.content, va.narrative_id FROM fingerprint f JOIN variant va ON va.id = f.variant_id"
    ).fetchall()
    assert rows == [("fetched block", narrative_id)]


def test_trustworthy_vote_creates_no_narrative(conn, vote):
    vote("trustworthy", ["s1"], content_hash="c1")
    fake = FakeAnalyzer()
    analyze.analyze_vote(conn, vote_id(conn, "c1"), fake)
    assert fake.calls == []
    assert narrative_of_vote(conn, vote_id(conn, "c1")) is None


def test_analysis_fails_closed_without_a_key(conn, vote, monkeypatch):
    monkeypatch.setattr(analyze, "settings", dataclasses.replace(analyze.settings, anthropic_api_key=None))
    vote("misleading", ["s1"], content_hash="c1")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        analyze.handle(conn, {"vote_id": vote_id(conn, "c1")})
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM narrative").fetchone()[0] == 0


class FakeAnthropic:
    """Stands in for anthropic.Anthropic, answering every request with the given content blocks."""

    def __init__(self, *content):
        self.requests = []
        self.content = content
        self.messages = self

    def __call__(self, **_client_options):
        return self

    def stream(self, **request):
        self.requests.append(request)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get_final_message(self):
        return SimpleNamespace(stop_reason="tool_use", content=list(self.content))


@pytest.fixture
def anthropic_answers(monkeypatch):
    monkeypatch.setattr(analyze, "settings", dataclasses.replace(analyze.settings, anthropic_api_key="key"))

    def answer(*content):
        fake = FakeAnthropic(*content)
        monkeypatch.setattr(analyze.anthropic, "Anthropic", fake)
        return fake

    return answer


def tool_call(analysis):
    return SimpleNamespace(type="tool_use", name="record_analysis", input=analysis)


def test_claude_analysis_is_read_from_a_streamed_messages_api_tool_call(anthropic_answers):
    fake = anthropic_answers(SimpleNamespace(type="text", text="Here it is."), tool_call(ANALYSIS))
    assert analyze.claude_analyze("Excerpt.", "misleading", None, "https://example.com") == ANALYSIS
    [request] = fake.requests
    assert set(request) == {"model", "max_tokens", "system", "messages", "tools"}


def test_claude_analysis_outside_the_schema_is_rejected(anthropic_answers):
    anthropic_answers(tool_call({**ANALYSIS, "techniques": ["mind_control"]}))
    with pytest.raises(ValueError, match="techniques"):
        analyze.claude_analyze("Excerpt.", "misleading", None, "https://example.com")


def test_claude_answer_without_a_tool_call_is_an_error(anthropic_answers):
    anthropic_answers(SimpleNamespace(type="text", text="I cannot help."))
    with pytest.raises(RuntimeError, match="record_analysis"):
        analyze.claude_analyze("Excerpt.", "misleading", None, "https://example.com")


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY is not set")
def test_claude_analyzes_a_manipulative_excerpt():
    result = analyze.claude_analyze(
        "Western elites are secretly planning to freeze Europeans this winter to punish them for doubting "
        "the sanctions. Only traitors still trust Brussels.",
        "manipulative",
        None,
        "https://example.com/article",
    )
    assert result["verdict"] in {"misinformation", "manipulative", "misleading"}
    assert set(result["techniques"]) <= set(analyze.TECHNIQUES)
    assert result["claim"] and result["title"] and result["explanation"]
