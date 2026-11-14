"""Collect EUvsDisinfo case titles from the case list and import them as title-only narratives.

The list at /disinformation-cases/ shows one page of cases per query, and robots.txt disallows its paging
and page-size parameters. A query filtered by `date=DD.MM.YYYY - DD.MM.YYYY` reports its total, so a range
whose total exceeds the page is split in half until every range fits. The site applies filters only
together with a keyword, and the keyword `a` matches every case. Requests run one at a time,
REQUEST_INTERVAL apart, under the hashtrust User-Agent; the importer never fetches case pages.

    uv run python -m app.seed.euvsdisinfo_list scrape [--start 2025-07-16] [--end YYYY-MM-DD]
    uv run python -m app.seed.euvsdisinfo_list import [--file PATH]
"""

import argparse
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx
from psycopg import Connection

from app.db import connection
from app.jobs import enqueue
from app.migrate import migrate
from app.seed.euvsdisinfo import _LOCK_KEY

LIST_URL = "https://euvsdisinfo.eu/disinformation-cases/"
TITLES_FILE = Path(__file__).resolve().parent / "data" / "euvsdisinfo-titles.jsonl"
USER_AGENT = "hashtrust/0.1 (EUvsDisinfo case list importer)"
REQUEST_INTERVAL = 2.0
RATE_LIMIT_RETRIES = 6
RATE_LIMIT_PAUSE = 60
# Day after the last case in the curated seed file.
DEFAULT_START = date(2025, 7, 16)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Case:
    title: str
    url: str
    date: str


class _ListParser(HTMLParser):
    """The result count and the case links of a case list page."""

    def __init__(self) -> None:
        super().__init__()
        self.total: int | None = None
        self.cases: list[Case] = []
        self._field: str | None = None
        self._count_text: list[str] = []
        self._href: str | None = None
        self._date: list[str] = []
        self._title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag == "a" and "b-archive__database-item" in classes:
            self._href, self._date, self._title = dict(attrs)["href"], [], []
        elif "b-archive__results-count" in classes:
            self._field = "count"
        elif self._href is not None and "b-archive__database-item-date" in classes:
            self._field = "date"
        elif self._href is not None and "b-archive__database-item-prefix" in classes:
            self._field = "prefix"
        elif self._href is not None and "b-archive__database-item-title" in classes:
            self._field = "title"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            day = datetime.strptime("".join(self._date).strip(), "%d.%m.%Y").date()
            title = " ".join("".join(self._title).split())
            self.cases.append(Case(title, urljoin(LIST_URL, self._href), day.isoformat()))
            self._href = None
        elif tag in ("span", "div"):
            if self._field == "count":
                self.total = int(re.sub(r"\D", "", "".join(self._count_text)) or 0)
            # The prefix span sits inside the title span, whose text continues after it.
            self._field = "title" if self._field == "prefix" else None

    def handle_data(self, data: str) -> None:
        if self._field == "count":
            self._count_text.append(data)
        elif self._field == "date":
            self._date.append(data)
        elif self._field == "title":
            self._title.append(data)


def parse_list(html: str) -> tuple[int, list[Case]]:
    """(total cases matching the query, the cases on this page) of a case list page."""
    parser = _ListParser()
    parser.feed(html)
    if parser.total is None:
        raise ValueError("no result count on the page; not a case list")
    return parser.total, parser.cases


def _fetch(client: httpx.Client, start: date, end: date) -> tuple[int, list[Case]]:
    """One list query, waiting out each 429 for its Retry-After or a doubling pause."""
    params = {"text": "a", "date": f"{start:%d.%m.%Y} - {end:%d.%m.%Y}"}
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        time.sleep(REQUEST_INTERVAL)
        response = client.get(LIST_URL, params=params)
        if response.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
            break
        retry_after = response.headers.get("Retry-After", "")
        pause = int(retry_after) if retry_after.isdigit() else RATE_LIMIT_PAUSE * 2**attempt
        log.info("rate limited; waiting %d s", pause)
        time.sleep(pause)
    response.raise_for_status()
    return parse_list(response.text)


def scrape(start: date, end: date) -> list[Case]:
    """Every listed case dated from start to end inclusive, sorted by date and URL."""
    found: dict[str, Case] = {}
    pending = [(start, end)]
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True) as client:
        while pending:
            lo, hi = pending.pop()
            total, cases = _fetch(client, lo, hi)
            if total > len(cases) and lo < hi:
                mid = lo + (hi - lo) // 2
                pending += [(mid + timedelta(days=1), hi), (lo, mid)]
                continue
            if total > len(cases):
                log.warning("%s lists %d cases but shows %d; the rest are missed", lo, total, len(cases))
            log.info("%s..%s: %d cases", lo, hi, len(cases))
            found.update((case.url, case) for case in cases)
    return sorted(found.values(), key=lambda c: (c.date, c.url))


def import_titles(conn: Connection, cases: list[dict]) -> int:
    """Create an active narrative per case not imported before, queue its claim vector, return the count."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
    created = []
    for case in cases:
        row = conn.execute(
            "INSERT INTO narrative (title, claim, verdict, source, source_url, state)"
            " SELECT %(title)s, %(title)s, 'misinformation', 'euvsdisinfo', %(url)s, 'active'"
            " WHERE NOT EXISTS (SELECT 1 FROM narrative WHERE source = 'euvsdisinfo' AND source_url = %(url)s)"
            " RETURNING id",
            case,
        ).fetchone()
        if row:
            created.append(row[0])
    if created:
        enqueue(conn, "narrative_vectors", {"narrative_ids": created})
    return len(created)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    scrape_cmd = commands.add_parser("scrape", help="collect case titles from euvsdisinfo.eu")
    scrape_cmd.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    scrape_cmd.add_argument("--end", type=date.fromisoformat, default=date.today())
    scrape_cmd.add_argument("--file", type=Path, default=TITLES_FILE)
    import_cmd = commands.add_parser("import", help="import collected titles as narratives")
    import_cmd.add_argument("--file", type=Path, default=TITLES_FILE)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    if args.command == "scrape":
        cases = scrape(args.start, args.end)
        with args.file.open("w") as f:
            f.writelines(json.dumps(asdict(case), ensure_ascii=False) + "\n" for case in cases)
        print(f"{len(cases)} cases from {args.start} to {args.end} written to {args.file}")
        return

    with args.file.open() as f:
        cases = [json.loads(line) for line in f if line.strip()]
    migrate()
    with connection() as conn:
        created = import_titles(conn, cases)
    print(f"{len(cases)} cases: {created} new narratives")


if __name__ == "__main__":
    main()
