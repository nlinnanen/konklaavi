import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.eval import run
from app.eval.metrics import Match, Page, metrics, report

MATCH = Match(text="Sanctions destroy Europe.", narrative_id="7", narrative="Sanctions")


def test_metrics_count_pages_by_label_and_flag():
    pages = [
        Page("p1", True, [MATCH]),
        Page("p2", True, [MATCH]),
        Page("p3", True, [MATCH]),
        Page("p4", True),
        Page("n1", False, [MATCH]),
        Page("n2", False),
        Page("n3", False),
        Page("n4", False),
    ]

    assert metrics(pages) == {
        "true_positives": 3,
        "false_negatives": 1,
        "false_positives": 1,
        "true_negatives": 3,
        "precision": 0.75,
        "recall": 0.75,
        "false_positive_rate": 0.25,
    }


def test_ratios_without_a_denominator_are_undefined():
    m = metrics([Page("p1", True), Page("n1", False)])

    assert (m["precision"], m["recall"], m["false_positive_rate"]) == (None, 0.0, 0.0)


def test_report_lists_each_false_positive_with_its_matches():
    pages = [Page("p1", True, [MATCH], case_id="c1"), Page("n1", False, [MATCH]), Page("n2", False)]

    assert report(pages)["false_positives"] == [
        {
            "url": "n1",
            "case_id": None,
            "matches": [{"text": "Sanctions destroy Europe.", "narrative_id": "7", "narrative": "Sanctions"}],
        }
    ]


def test_lookup_requests_stay_within_the_hash_limit_and_cover_every_block(monkeypatch):
    monkeypatch.setattr(run, "MAX_LOOKUP_HASHES", 3)
    blocks = [
        {"hash": f"b{i}", "content": "", "sentences": [f"s{i}a", f"s{i}b"], "bands": ["x"] if i % 2 else None}
        for i in range(4)
    ]

    bodies = list(run.lookup_requests(blocks))

    assert all(len(body["hashes"]) <= 3 for body in bodies)
    assert sorted(h for body in bodies for h in body["hashes"]) == sorted(
        h for block in blocks for h in block["sentences"]
    )
    assert [b["hash"] for body in bodies for b in body["blocks"]] == ["b1", "b3"]


def insert(conn, sql, *params):
    return conn.execute(sql + " RETURNING id", params).fetchone()[0]


@pytest.fixture
def fetched_page(conn):
    """A fetched page with a block of two sentences in an active narrative and a clean block."""
    narrative = insert(
        conn,
        "INSERT INTO narrative (title, source, state) VALUES ('Sanctions backfire', 'euvsdisinfo', 'active')",
    )
    variant = insert(conn, "INSERT INTO variant (narrative_id) VALUES (%s)", narrative)
    domain = insert(conn, "INSERT INTO domain (name) VALUES ('news.example')")
    page = insert(
        conn, "INSERT INTO page (url, domain_id, status) VALUES ('https://news.example/a', %s, 'ok')", domain
    )
    for content_hash, content, variant_id in [
        ("flagged", "Sanctions only hurt Europe.", variant),
        ("clean", "The weather was mild.", None),
    ]:
        fingerprint = insert(
            conn,
            "INSERT INTO fingerprint (content_hash, content, variant_id) VALUES (%s, %s, %s)",
            content_hash,
            content,
            variant_id,
        )
        for n in range(2):
            conn.execute(
                "INSERT INTO sentence (hash, fingerprint_id) VALUES (%s, %s)",
                (f"s{n}-{content_hash}", fingerprint),
            )
        conn.execute(
            "INSERT INTO observation (seen_at, fingerprint_id, page_id, source) VALUES (now(), %s, %s, 'fetch')",
            (fingerprint, page),
        )
    conn.commit()
    return narrative


def test_fetched_page_gets_one_match_per_flagged_block_with_its_narrative(conn, fetched_page):
    blocks = run.page_blocks(conn, "https://news.example/a")

    with TestClient(app) as client:
        matches = run.page_matches(client, blocks)

    assert matches == [Match("Sanctions only hurt Europe.", str(fetched_page), "Sanctions backfire")]


def test_page_that_was_not_fetched_has_no_blocks(conn):
    assert run.page_blocks(conn, "https://news.example/missing") is None
