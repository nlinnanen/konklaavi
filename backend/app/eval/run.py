"""Evaluate page-level detection on the held-out EUvsDisinfo cases and on reliable-outlet negatives.

python -m app.eval [--api URL] [--timeout SECONDS]

Fetches go through the worker's fetch job, so the worker must be running. Lookups go to the API at
--api, or to the API app in-process.
"""

import argparse
import json
import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from psycopg import Connection

from app.api.main import app
from app.db import connection
from app.eval.metrics import Match, Page, report
from app.jobs import enqueue
from app.migrate import migrate
from app.seed.euvsdisinfo import is_test_case, load_cases

HERE = Path(__file__).resolve().parent
NEGATIVES_FILE = HERE / "negatives.txt"
RESULTS_FILE = HERE / "results" / "latest.json"
MAX_LOOKUP_HASHES = 500
MATCH_TEXT_CHARS = 300
POLL_SECONDS = 5

log = logging.getLogger(__name__)


def load_negatives(path: Path = NEGATIVES_FILE) -> list[str]:
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def load_positives(conn: Connection) -> tuple[dict[str, str], int]:
    """Article URL -> case id for the test cases, leaving out URLs a seed case shares; and how many were."""
    seed_urls = {url for case in load_cases() if not is_test_case(case["id"]) for url in case["article_urls"]}
    positives: dict[str, str] = {}
    overlap = set()
    for case_id, urls in conn.execute("SELECT case_id, article_urls FROM eval_case ORDER BY case_id"):
        for url in urls:
            if url in seed_urls:
                overlap.add(url)
            else:
                positives.setdefault(url, case_id)
    return positives, len(overlap)


def fetch_all(urls: list[str], timeout: float) -> None:
    """Enqueue a fetch without a narrative for each page not fetched yet and wait for those jobs."""
    with connection() as conn:
        fetched = {
            row[0]
            for row in conn.execute(
                "SELECT url FROM page WHERE url = ANY(%s) AND fetched_at IS NOT NULL", (urls,)
            )
        }
        job_ids = [
            enqueue(conn, "fetch", {"url": url, "reason": "eval"}) for url in urls if url not in fetched
        ]
    log.info("%d pages already fetched, %d fetch jobs enqueued", len(fetched), len(job_ids))
    deadline = time.monotonic() + timeout
    while job_ids:
        with connection() as conn:
            pending = conn.execute(
                "SELECT count(*) FROM job WHERE id = ANY(%s) AND state IN ('queued', 'running')", (job_ids,)
            ).fetchone()[0]
        if pending == 0:
            return
        if time.monotonic() > deadline:
            log.warning("%d fetch jobs still pending after %.0f s", pending, timeout)
            return
        log.info("waiting for %d of %d fetch jobs", pending, len(job_ids))
        time.sleep(POLL_SECONDS)


def page_blocks(conn: Connection, url: str) -> list[dict] | None:
    """The fetched page's blocks as {hash, content, sentences, bands}, or None when it was not fetched."""
    rows = conn.execute(
        """
        SELECT DISTINCT f.id, f.content_hash, f.content
        FROM page p JOIN observation o ON o.page_id = p.id JOIN fingerprint f ON f.id = o.fingerprint_id
        WHERE p.url = %s AND p.status = 'ok'
        ORDER BY f.id
        """,
        (url,),
    ).fetchall()
    if not rows:
        return None
    ids = [row[0] for row in rows]
    sentences: dict[int, list[str]] = {}
    for fingerprint_id, hash in conn.execute(
        "SELECT fingerprint_id, hash FROM sentence WHERE fingerprint_id = ANY(%s)", (ids,)
    ):
        sentences.setdefault(fingerprint_id, []).append(hash)
    bands: dict[int, list[str]] = {}
    for fingerprint_id, hash in conn.execute(
        "SELECT fingerprint_id, hash FROM lsh_band WHERE fingerprint_id = ANY(%s) ORDER BY fingerprint_id, band",
        (ids,),
    ):
        bands.setdefault(fingerprint_id, []).append(hash)
    return [
        {"hash": content_hash, "content": content, "sentences": sentences.get(id, []), "bands": bands.get(id)}
        for id, content_hash, content in rows
    ]


def lookup_requests(blocks: list[dict]) -> Iterator[dict]:
    """/lookup bodies covering every block, each within the API's hash limit."""
    hashes: list[str] = []
    with_bands: list[dict] = []
    for block in blocks:
        if hashes and len(hashes) + len(block["sentences"]) > MAX_LOOKUP_HASHES:
            yield {"hashes": hashes, "blocks": with_bands}
            hashes, with_bands = [], []
        hashes += block["sentences"][:MAX_LOOKUP_HASHES]
        if block["bands"]:
            with_bands.append({"hash": block["hash"], "bands": block["bands"]})
    if hashes or with_bands:
        yield {"hashes": hashes, "blocks": with_bands}


def page_matches(client: httpx.Client, blocks: list[dict]) -> list[Match]:
    """One match per flagged block and narrative, carrying the block's text."""
    content = {block["hash"]: block["content"] for block in blocks}
    content.update({h: block["content"] for block in blocks for h in block["sentences"]})
    matches = []
    for body in lookup_requests(blocks):
        response = client.post("/lookup", json=body)
        response.raise_for_status()
        for result in response.json()["results"]:
            if not result["flagged"]:
                continue
            signature = result.get("signature") or {}
            matches.append(
                Match(
                    text=content[result["hash"]][:MATCH_TEXT_CHARS],
                    narrative_id=signature.get("narrativeId"),
                    narrative=signature.get("title"),
                )
            )
    return list(dict.fromkeys(matches))


def evaluate(client: httpx.Client, timeout: float) -> dict:
    migrate()
    with connection() as conn:
        positives, overlap = load_positives(conn)
        cases = conn.execute("SELECT count(*) FROM eval_case").fetchone()[0]
    negatives = [url for url in load_negatives() if url not in positives]
    labelled = [(url, True, positives[url]) for url in positives] + [(url, False, None) for url in negatives]
    fetch_all([url for url, _, _ in labelled], timeout)

    pages: list[Page] = []
    unreachable: list[str] = []
    for url, positive, case_id in labelled:
        with connection() as conn:
            blocks = page_blocks(conn, url)
        if blocks is None:
            unreachable.append(url)
            continue
        pages.append(Page(url, positive, page_matches(client, blocks), case_id))

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "positives": {
            "cases": cases,
            "urls": len(positives),
            "seed_overlap": overlap,
            "pages": sum(p.positive for p in pages),
        },
        "negatives": {"urls": len(negatives), "pages": sum(not p.positive for p in pages)},
        **report(pages),
        "unreachable": unreachable,
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def print_table(results: dict) -> None:
    m = results["metrics"]
    pos, neg = results["positives"], results["negatives"]
    print(
        f"positives: {pos['pages']} pages of {pos['urls']} URLs from {pos['cases']} cases"
        f" ({pos['seed_overlap']} URLs shared with seed cases left out)"
    )
    print(f"negatives: {neg['pages']} pages of {neg['urls']} URLs")
    print()
    print(f"{'':10}{'flagged':>10}{'not flagged':>14}")
    print(f"{'positive':10}{m['true_positives']:>10}{m['false_negatives']:>14}")
    print(f"{'negative':10}{m['false_positives']:>10}{m['true_negatives']:>14}")
    print()
    print(
        f"precision {_percent(m['precision'])}   recall {_percent(m['recall'])}"
        f"   false-positive rate {_percent(m['false_positive_rate'])}"
    )
    for page in results["false_positives"]:
        print(f"\nfalse positive: {page['url']}")
        for match in page["matches"]:
            print(f"  [{match['narrative'] or 'votes'}] {match['text']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", help="base URL of a running API; the API app runs in-process without it")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait for fetch jobs")
    parser.add_argument("--out", type=Path, default=RESULTS_FILE, help="where to write the JSON results")
    args = parser.parse_args()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.setLevel(logging.INFO)
    if args.api:
        client = httpx.Client(base_url=args.api, timeout=60)
    else:
        client = TestClient(app)
    with client:
        results = evaluate(client, args.timeout)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print_table(results)
    print(f"\nwrote {args.out}")
