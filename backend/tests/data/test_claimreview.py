from datetime import date
from pathlib import Path

from app.seed.claimreview import feed_items, select_claims

FIXTURE = Path(__file__).parent / "fixtures" / "claimreview_feed.json"


def test_keeps_recent_negative_in_domain_claims_with_a_fetchable_appearance():
    # A small chunk size splits feed elements across reads.
    claims, _ = select_claims(feed_items(FIXTURE, chunk_size=64), since=date(2025, 7, 1))

    assert [(c.review_url, c.verdict, c.urls) for c in claims] == [
        (
            "https://du-check.eu/provjera/britanija",
            "manipulative",
            ["https://www.logicno.com/politika/britanija"],
        ),
        ("https://factly.in/rice-ban", "misleading", ["https://example.in/rice-ban"]),
        ("https://azbukamedia.com/sanctions", "misinformation", ["https://dzen.ru/a/sanctions"]),
    ]
