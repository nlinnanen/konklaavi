import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import psycopg

_spec = importlib.util.spec_from_file_location(
    "dashboard_queries", Path(__file__).resolve().parents[3] / "dashboard" / "queries.py"
)
queries = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(queries)


def insert(conn, sql, *params):
    return conn.execute(sql + " RETURNING id", params).fetchone()[0]


def add_note(conn, narrative, classification, status, moderation="visible"):
    account = conn.execute(
        "INSERT INTO user_account (email) VALUES (gen_random_uuid() || '@example.com') RETURNING id"
    ).fetchone()[0]
    author = conn.execute(
        "INSERT INTO install (id, account_id) VALUES (gen_random_uuid(), %s) RETURNING id", (account,)
    ).fetchone()[0]
    return insert(
        conn,
        "INSERT INTO note (narrative_id, author_install, account_id, classification, body, status, moderation)"
        " VALUES (%s, %s, %s, %s, 'Context.', %s, %s)",
        narrative,
        author,
        account,
        classification,
        status,
        moderation,
    )


def states(conn):
    rows = conn.execute(queries.NARRATIVES, {"sources": ["note"]}).fetchall()
    return {title: (state, visible, pending) for _, title, _, state, visible, pending, *_ in rows}


def test_narrative_state_follows_its_strongest_visible_note(conn):
    notes = {
        "Misleading": [
            ("not_misleading", "helpful"),
            ("misleading", "helpful"),
            ("misleading", "controversial"),
        ],
        "Context": [("not_misleading", "helpful"), ("misleading", "needs_more_ratings")],
        "Controversial": [("misleading", "controversial"), ("misleading", "needs_more_ratings")],
        "Needs ratings": [("misleading", "needs_more_ratings"), ("misleading", "not_helpful")],
        "Not helpful": [("misleading", "not_helpful")],
        "Held": [],
        "Tracked": [],
    }
    for title, title_notes in notes.items():
        narrative = insert(conn, "INSERT INTO narrative (title, source) VALUES (%s, 'note')", title)
        for classification, status in title_notes:
            add_note(conn, narrative, classification, status)
        if title == "Held":
            add_note(conn, narrative, "misleading", "helpful", moderation="pending_review")
            add_note(conn, narrative, "misleading", "helpful", moderation="hidden")
    insert(conn, "INSERT INTO narrative (title, source) VALUES ('Candidate', 'cluster')")

    assert states(conn) == {
        "Misleading": ("misleading", 3, 0),
        "Context": ("context", 2, 0),
        "Controversial": ("controversial", 2, 0),
        "Needs ratings": ("needs_ratings", 2, 0),
        "Not helpful": ("tracked", 1, 0),
        "Held": ("tracked", 0, 1),
        "Tracked": ("tracked", 0, 0),
    }


def test_narrative_row_counts_its_spread(conn):
    _, fingerprints = add_spread(conn)
    add_second_domain(conn, fingerprints[0])

    rows = conn.execute(queries.NARRATIVES, {"sources": ["note"]}).fetchall()

    assert [row[6:9] for row in rows] == [(1, 3, 2)]


def test_an_account_counts_once_and_an_install_without_one_on_its_own(conn):
    narrative = insert(conn, "INSERT INTO narrative (title, source) VALUES ('Rated', 'note')")
    note = add_note(conn, narrative, "misleading", "needs_more_ratings")
    account = insert(conn, "INSERT INTO user_account (email) VALUES ('a@example.com')")
    for helpfulness, account_id in [("helpful", account), ("somewhat", account), ("not_helpful", None)]:
        install = conn.execute(
            "INSERT INTO install (id, account_id) VALUES (gen_random_uuid(), %s) RETURNING id", (account_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO note_rating (note_id, install, helpfulness) VALUES (%s, %s, %s)",
            (note, install, helpfulness),
        )

    row = conn.execute(queries.RECENT_NOTES, {"limit": 10}).fetchone()
    columns = [c.name for c in conn.execute(queries.RECENT_NOTES, {"limit": 10}).description]
    counts = dict(zip(columns, row, strict=True))

    assert [counts[k] for k in ("ratings", "counted", "helpful", "somewhat", "not_helpful")] == [
        3,
        2,
        1,
        1,
        1,
    ]


def add_spread(conn):
    """A narrative with two fingerprints, the second derived from the first, seen on two pages."""
    narrative = insert(conn, "INSERT INTO narrative (title, source) VALUES ('Spread', 'note')")
    variant = insert(conn, "INSERT INTO variant (narrative_id) VALUES (%s)", narrative)
    domain = insert(conn, "INSERT INTO domain (name) VALUES ('a.example')")
    pages = [
        insert(conn, "INSERT INTO page (url, domain_id) VALUES (%s, %s)", f"https://a.example/{i}", domain)
        for i in range(2)
    ]
    fingerprints = [
        insert(
            conn,
            "INSERT INTO fingerprint (content_hash, content, variant_id, first_page_id) VALUES (%s, %s, %s, %s)",
            f"h{i}",
            f"Block {i}",
            variant,
            page,
        )
        for i, page in enumerate(pages)
    ]
    for day, fingerprint, page in [(1, 0, 0), (1, 0, 1), (2, 1, 1)]:
        conn.execute(
            "INSERT INTO observation (seen_at, fingerprint_id, page_id, source) VALUES (%s, %s, %s, 'fetch')",
            (datetime(2025, 7, day, 12, tzinfo=UTC), fingerprints[fingerprint], pages[page]),
        )
    conn.execute(
        "INSERT INTO edge (src_kind, src, dst_kind, dst, type) VALUES ('fingerprint', %s, 'fingerprint', %s,"
        " 'DERIVED_FROM')",
        (fingerprints[1], fingerprints[0]),
    )
    return narrative, fingerprints


def test_narrative_pages_count_observations_per_page(conn):
    narrative, _ = add_spread(conn)

    rows = conn.execute(queries.NARRATIVE_PAGES, {"id": narrative}).fetchall()

    assert [(url, domain, count) for url, domain, _, count, _, _ in rows] == [
        ("https://a.example/0", "a.example", 1),
        ("https://a.example/1", "a.example", 2),
    ]


def test_narrative_lineage_points_from_parent_to_derived_block(conn):
    narrative, (first, second) = add_spread(conn)

    rows = conn.execute(queries.NARRATIVE_LINEAGE, {"id": narrative, "limit": 10}).fetchall()

    assert rows == [(first, "a.example: Block 0", second, "a.example: Block 1")]


def add_second_domain(conn, fingerprint):
    """A page on b.example observed on day 3 with the given fingerprint."""
    domain = insert(conn, "INSERT INTO domain (name) VALUES ('b.example')")
    page = insert(conn, "INSERT INTO page (url, domain_id) VALUES ('https://b.example/0', %s)", domain)
    conn.execute(
        "INSERT INTO observation (seen_at, fingerprint_id, page_id, source) VALUES (%s, %s, %s, 'fetch')",
        (datetime(2025, 7, 3, 12, tzinfo=UTC), fingerprint, page),
    )


def test_spread_counts_observations_and_domains_reached_per_day(conn, database_url):
    narrative, fingerprints = add_spread(conn)
    add_second_domain(conn, fingerprints[0])
    conn.commit()
    # Another test's refresh leaves a watermark that hides these older rows from real-time aggregation.
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("CALL refresh_continuous_aggregate('narrative_domain_daily', NULL, NULL)")

    per_day = conn.execute(queries.SPREAD_PER_DAY, {"id": narrative}).fetchall()

    assert [(day.day, *counts) for day, *counts in per_day] == [(1, 2, 1, 1), (2, 1, 1, 1), (3, 1, 1, 2)]
