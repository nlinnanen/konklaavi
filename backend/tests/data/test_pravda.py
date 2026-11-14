from datetime import UTC, datetime
from pathlib import Path

from app.seed.pravda import parse_news_sitemap

FIXTURE = Path(__file__).parent / "fixtures" / "pravda_sitemap_news.xml"


def test_news_sitemap_yields_articles_since_the_cutoff_newest_first():
    entries = parse_news_sitemap(FIXTURE.read_bytes(), since=datetime(2026, 11, 7, tzinfo=UTC))

    assert [e.url for e in entries] == [
        "https://francais.news-pravda.com/world/2026/11/14/1073469.html",
        "https://francais.news-pravda.com/world/2026/11/14/1073468.html",
    ]
