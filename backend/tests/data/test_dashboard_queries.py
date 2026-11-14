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
