import importlib.util
from datetime import UTC, datetime
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "dashboard_queries", Path(__file__).resolve().parents[3] / "dashboard" / "queries.py"
)
queries = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(queries)


def insert(conn, sql, *params):
    return conn.execute(sql + " RETURNING id", params).fetchone()[0]


def test_narrative_row_counts_its_spread(conn):
    spread = insert(conn, "INSERT INTO narrative (title, source) VALUES ('Spread', 'euvsdisinfo')")
    insert(conn, "INSERT INTO narrative (title, source) VALUES ('Unseen', 'report')")
    domains = [
        insert(conn, "INSERT INTO domain (name) VALUES (%s)", name) for name in ("a.example", "b.example")
    ]
    pages = [
        insert(conn, "INSERT INTO page (url, domain_id) VALUES (%s, %s)", f"https://p/{i}", domain)
        for i, domain in enumerate([domains[0], domains[0], domains[1]])
    ]
    first_seen = datetime(2025, 7, 1, tzinfo=UTC)
    for i, page_ids in enumerate([pages[:2], pages[1:]]):
        variant = insert(conn, "INSERT INTO variant (narrative_id) VALUES (%s)", spread)
        fingerprint = insert(
            conn,
            "INSERT INTO fingerprint (content_hash, content, variant_id, first_seen) VALUES (%s, '', %s, %s)",
            f"h{i}",
            variant,
            first_seen.replace(day=1 + i),
        )
        for page in page_ids:
            conn.execute(
                "INSERT INTO observation (seen_at, fingerprint_id, page_id, source) VALUES (now(), %s, %s, 'fetch')",
                (fingerprint, page),
            )

    rows = conn.execute(queries.NARRATIVES).fetchall()

    assert [row[1:] for row in rows] == [
        ("Spread", "euvsdisinfo", "candidate", 2, 3, 2, first_seen),
        ("Unseen", "report", "candidate", 0, 0, 0, None),
    ]


def add_spread(conn):
    """A narrative with two fingerprints, the second derived from the first, seen on two pages."""
    narrative = insert(conn, "INSERT INTO narrative (title, source) VALUES ('Spread', 'euvsdisinfo')")
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


def test_narrative_observations_are_bucketed_by_day(conn):
    narrative, _ = add_spread(conn)

    rows = conn.execute(queries.NARRATIVE_OBSERVATIONS_PER_DAY, {"id": narrative}).fetchall()

    assert [(day.day, count) for day, count in rows] == [(1, 2), (2, 1)]


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


def test_spread_counts_observations_and_domains_reached_per_day(conn):
    narrative, fingerprints = add_spread(conn)
    add_second_domain(conn, fingerprints[0])

    per_day = conn.execute(queries.SPREAD_PER_DAY, {"id": narrative}).fetchall()
    domains = conn.execute(queries.SPREAD_DOMAINS, {"id": narrative}).fetchall()

    assert [(day.day, *counts) for day, *counts in per_day] == [(1, 2, 1, 1), (2, 1, 1, 1), (3, 1, 1, 2)]
    assert [(name, first.day, last.day, n) for name, first, last, n in domains] == [
        ("a.example", 1, 2, 3),
        ("b.example", 3, 3, 1),
    ]
