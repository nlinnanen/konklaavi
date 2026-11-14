import pytest

from app import fpclient
from app.jobs.variants import boilerplate, is_boilerplate

ORIGINAL = (
    "Western sanctions against Russia have backfired and are destroying the European economy, while ordinary "
    "citizens in Germany and France pay record prices for gas and electricity because their governments "
    "follow orders from Washington instead of defending national interests."
)
EDITED = ORIGINAL.replace("record prices", "insane prices")
DATED = "Updated {} at {}. Subscribe to our channel for daily news and analysis from the whole region."


def add_fingerprint(conn, text):
    block = fpclient.fingerprint([text])["blocks"][0]
    fingerprint_id = conn.execute(
        "INSERT INTO fingerprint (content_hash, content, minhash) VALUES (%s, %s, %s) RETURNING id",
        (block["contentHash"], text, block["minhash"]),
    ).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO lsh_band (band, hash, fingerprint_id) VALUES (%s, %s, %s)",
            [(i, h, fingerprint_id) for i, h in enumerate(block["bands"])],
        )
    return fingerprint_id


def observe(conn, fingerprint_id, url):
    domain = url.split("/")[2]
    domain_id = conn.execute(
        "INSERT INTO domain (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (domain,),
    ).fetchone()[0]
    page_id = conn.execute(
        "INSERT INTO page (url, domain_id, status) VALUES (%s, %s, 'ok')"
        " ON CONFLICT (url) DO UPDATE SET url = EXCLUDED.url RETURNING id",
        (url, domain_id),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO observation (seen_at, fingerprint_id, page_id, source) VALUES (now(), %s, %s, 'fetch')",
        (fingerprint_id, page_id),
    )


def test_text_on_one_page_is_not_boilerplate(conn):
    original = add_fingerprint(conn, ORIGINAL)
    observe(conn, original, "https://news.example/a")

    assert not is_boilerplate(conn, original)


def test_an_edited_copy_on_another_page_of_the_domain_makes_both_boilerplate(conn):
    original, edited = add_fingerprint(conn, ORIGINAL), add_fingerprint(conn, EDITED)
    observe(conn, original, "https://news.example/a")
    observe(conn, edited, "https://news.example/b")

    assert boilerplate(conn, [original, edited]) == {original, edited}


def test_text_differing_only_in_dates_and_numbers_is_boilerplate(conn):
    first = add_fingerprint(conn, DATED.format("12 March 2025", "10:42"))
    second = add_fingerprint(conn, DATED.format("3 April 2026", "18:05"))
    observe(conn, first, "https://news.example/a")
    observe(conn, second, "https://news.example/b")

    assert is_boilerplate(conn, first)


def test_copies_on_another_domain_do_not_make_the_original_boilerplate(conn):
    original, edited = add_fingerprint(conn, ORIGINAL), add_fingerprint(conn, EDITED)
    observe(conn, original, "https://origin.example/a")
    observe(conn, edited, "https://mirror.example/a")
    observe(conn, edited, "https://mirror.example/b")

    assert boilerplate(conn, [original, edited]) == {edited}


@pytest.mark.parametrize(
    "text",
    [
        "Officials refused to comment on the reports.",
        (
            "Results: 12 March 2025, 3 April 2025, 9 May 2025, 1 June 2025 and 14 July 2025 for the regional"
            " council election in the north."
        ),
    ],
)
def test_short_or_mostly_dated_text_is_boilerplate(conn, text):
    fingerprint_id = add_fingerprint(conn, text)
    observe(conn, fingerprint_id, "https://news.example/a")

    assert is_boilerplate(conn, fingerprint_id)
