from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.eval.benchmark import run
from app.eval.benchmark.cases import import_cases, load_cases, split_urls
from app.eval.benchmark.metrics import Match, Page, best_threshold, metrics, report, sweep

FIXTURE = Path(__file__).parent / "fixtures" / "euvsdisinfo.jsonl"

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
            "matches": [{"text": "Sanctions destroy Europe.", "narrative_id": "7", "narrative": "Sanctions"}],
        }
    ]


def test_report_lists_each_false_negative_with_its_reason():
    pages = [
        Page("p1", True, case_id="c1", reason="no narrative"),
        Page("p2", True, [MATCH]),
        Page("n1", False),
    ]

    assert report(pages)["false_negatives"] == [{"url": "p1", "case_id": "c1", "reason": "no narrative"}]


def test_a_page_matched_only_by_other_narratives_is_not_flagged_by_its_own():
    other = Page("p1", True, [MATCH], narrative_id="8")
    own = Page("p2", True, [MATCH], narrative_id="7")

    assert (other.flagged_by_own_narrative, own.flagged_by_own_narrative) == (False, True)


def near(cosine: float) -> list[Match]:
    return [Match("Sanctions hurt Europe more than Russia.", "7", "Sanctions", cosine)]


def test_sweep_flags_narrative_level_matches_at_or_above_each_threshold_and_picks_the_best():
    known = [Page("p1", True, [MATCH]), Page("p2", True, near=near(0.8)), Page("p3", True, near=near(0.6))]
    negatives = [Page("n1", False, near=near(0.75)), Page("n2", False)]

    rows = sweep({"known": known}, negatives, [0.7, 0.8, 0.9])

    assert rows == [
        {"threshold": 0.7, "false_positive_rate": 0.5, "known_precision": 2 / 3, "known_recall": 2 / 3},
        {"threshold": 0.8, "false_positive_rate": 0.0, "known_precision": 1.0, "known_recall": 2 / 3},
        {"threshold": 0.9, "false_positive_rate": 0.0, "known_precision": 1.0, "known_recall": 1 / 3},
    ]
    assert best_threshold(rows, "known_recall", 0.01) == 0.8


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
    """A fetched page with a block of two sentences in a note narrative and a clean block."""
    narrative = insert(
        conn,
        "INSERT INTO narrative (title, source, state) VALUES ('Sanctions backfire', 'note', 'active')",
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
        matches, flagged = run.page_matches(client, blocks)

    assert matches == [Match("Sanctions only hurt Europe.", str(fetched_page), "Sanctions backfire")]
    assert flagged == {"flagged"}


def test_page_that_was_not_fetched_has_no_blocks(conn):
    assert run.page_blocks(conn, "https://news.example/missing") is None


def test_held_out_urls_are_known_positives_and_test_cases_unseen_ones(conn):
    cases = load_cases(FIXTURE)
    # case-2 is a test case; give it a URL case-1 seeds, which leaves the evaluation.
    cases[1] = {**cases[1], "article_urls": [*cases[1]["article_urls"], "https://news.example/1/a"]}
    import_cases(conn, cases)
    narrative = conn.execute("SELECT id FROM narrative WHERE source_url LIKE '%%case-3/'").fetchone()[0]

    labels = run.label_urls(conn, cases)

    assert labels.known == {url: ("case-3", str(narrative)) for url in split_urls(cases[2])[1]}
    assert labels.unseen == {"https://news.example/2/a": "case-2"}
    assert (labels.known_cases, labels.unseen_cases, labels.unseen_overlap) == (1, 1, 1)


def add_page(conn, url, blocks):
    """A fetched page holding the given fingerprints, created by content when new."""
    domain = url.split("/")[2]
    conn.execute("INSERT INTO domain (name) VALUES (%s) ON CONFLICT DO NOTHING", (domain,))
    page = insert(
        conn,
        "INSERT INTO page (url, domain_id, status) SELECT %s, id, 'ok' FROM domain WHERE name = %s",
        url,
        domain,
    )
    for content in blocks:
        fingerprint = conn.execute(
            "INSERT INTO fingerprint (content_hash, content) VALUES (md5(%s), %s)"
            " ON CONFLICT (content_hash) DO UPDATE SET content = EXCLUDED.content RETURNING id",
            (content, content),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO observation (seen_at, fingerprint_id, page_id, source) VALUES (now(), %s, %s, 'fetch')",
            (fingerprint, page),
        )
    return page


ARTICLE = [
    "The first paragraph of the article makes a claim about sanctions.",
    "The second paragraph of the article repeats the same claim at length.",
    "The third paragraph of the article quotes an official who made the claim.",
]


def test_a_page_repeating_most_of_a_held_out_page_on_another_domain_is_its_mirror(conn):
    add_page(conn, "https://a.example/held", [*ARTICLE, "Share this"])
    add_page(conn, "https://b.example/copy", ARTICLE)
    add_page(conn, "https://c.example/quote", ARTICLE[:1])
    add_page(conn, "https://d.example/labelled", ARTICLE)
    add_page(conn, "https://a.example/same-domain", ARTICLE)

    mirrors = run.find_mirrors(conn, ["https://a.example/held"], {"https://d.example/labelled"})

    assert mirrors == {"https://b.example/copy": "https://a.example/held"}


def test_a_miss_names_the_case_narrative_without_blocks(conn):
    narrative = insert(conn, "INSERT INTO narrative (title, source) VALUES ('Empty', 'note')")
    add_page(conn, "https://a.example/held", ARTICLE)

    reason = run.miss_reason(conn, "https://a.example/held", str(narrative))

    assert "no blocks" in reason


def test_a_miss_reports_the_closest_block_to_the_case_narrative(conn):
    narrative = insert(conn, "INSERT INTO narrative (title, source) VALUES ('Claim', 'note')")
    variant = insert(conn, "INSERT INTO variant (narrative_id) VALUES (%s)", narrative)
    page = add_page(conn, "https://a.example/held", ARTICLE)
    unit = [1.0] + [0.0] * 383
    tilted = [0.6, 0.8] + [0.0] * 382
    conn.execute(
        "UPDATE fingerprint SET embedding = %s::real[]::vector"
        " WHERE id IN (SELECT fingerprint_id FROM observation WHERE page_id = %s)",
        (tilted, page),
    )
    insert(
        conn,
        "INSERT INTO fingerprint (content_hash, content, embedding, variant_id)"
        " VALUES ('seed', 'Seed block.', %s::real[]::vector, %s)",
        unit,
        variant,
    )

    reason = run.miss_reason(conn, "https://a.example/held", str(narrative))

    assert "(1 blocks)" in reason and "cosine 0.60" in reason
