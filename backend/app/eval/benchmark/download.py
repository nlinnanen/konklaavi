"""Build the benchmark's EUvsDisinfo case file from the published dataset.

The rows come from the Hugging Face dataset NLP-UniBW/eu_vs_disinfo, a scrape of the euvsdisinfo.eu case
database, read through the datasets-server rows API. euvsdisinfo.eu itself sits behind a bot block, so the
importer never contacts it.

    uv run python -m app.eval.benchmark download --cases 400
"""

import argparse
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.eval.benchmark.cases import SEED_FILE

ROWS_URL = "https://datasets-server.huggingface.co/rows"
DATASET = "NLP-UniBW/eu_vs_disinfo"
PAGE_SIZE = 100
# Pause between requests to the rows API.
REQUEST_INTERVAL = 1.0
USER_AGENT = "konklaavi/0.1 (benchmark)"


class _Paragraphs(HTMLParser):
    """Text of an HTML fragment with one entry per block element."""

    _BLOCKS = frozenset({"p", "li", "div", "br", "h1", "h2", "h3", "h4", "blockquote"})

    def __init__(self) -> None:
        super().__init__()
        self.paragraphs: list[str] = []
        self._current: list[str] = []

    def handle_data(self, data: str) -> None:
        self._current.append(data)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._BLOCKS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCKS:
            self._flush()

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._current)).strip()
        # The source HTML is pretty-printed, which leaves a space between a link and the punctuation after it.
        text = re.sub(r" ([.,;:!?)])", r"\1", text)
        if text:
            self.paragraphs.append(text)
        self._current = []


def html_to_text(html: str) -> str:
    parser = _Paragraphs()
    parser.feed(html)
    parser.close()
    return "\n\n".join(parser.paragraphs)


def to_case(row: dict) -> dict | None:
    """A case from a dataset row, or None when the row lacks a claim, a disproof or an article URL."""
    urls = [u.strip() for u in row.get("link_original") or []]
    urls = list(dict.fromkeys(u for u in urls if urlsplit(u).scheme in ("http", "https")))
    disproof = html_to_text(row.get("response_html") or "") or (row.get("response") or "").strip()
    case_url = row.get("link") or ""
    case_id = urlsplit(case_url).path.rstrip("/").rsplit("/", 1)[-1]
    if not (case_id and row.get("title") and row.get("summary") and disproof and urls):
        return None
    return {
        "id": case_id,
        "title": row["title"].strip(),
        "summary": row["summary"].strip(),
        "disproof": disproof,
        "url": case_url,
        "article_urls": urls,
        "languages": [lang for lang in row.get("article_language") or [] if lang],
        "date": (row.get("publication_date") or "")[:10] or None,
    }


def _rows(client: httpx.Client, offset: int, attempts: int = 4) -> list[dict]:
    params = {
        "dataset": DATASET,
        "config": "default",
        "split": "train",
        "offset": offset,
        "length": PAGE_SIZE,
    }
    for attempt in range(1, attempts + 1):
        response = client.get(ROWS_URL, params=params)
        # The rows API answers 5xx now and then under load.
        if response.status_code < 500 or attempt == attempts:
            response.raise_for_status()
            return response.json()["rows"]
        time.sleep(REQUEST_INTERVAL * 2**attempt)
    raise AssertionError("unreachable")


def download(count: int) -> list[dict]:
    """The newest `count` usable cases; the dataset lists cases newest first."""
    cases: list[dict] = []
    seen: set[str] = set()
    offset = 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        while len(cases) < count:
            if offset:
                time.sleep(REQUEST_INTERVAL)
            rows = _rows(client, offset)
            if not rows:
                break
            for item in rows:
                case = to_case(item["row"])
                if case and case["id"] not in seen:
                    seen.add(case["id"])
                    cases.append(case)
            offset += len(rows)
    return cases[:count]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cases", type=int, default=400, help="number of cases to keep")
    parser.add_argument("--out", type=Path, default=SEED_FILE)
    args = parser.parse_args()
    cases = download(args.cases)
    with args.out.open("w") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"wrote {len(cases)} cases to {args.out}")


if __name__ == "__main__":
    main()
