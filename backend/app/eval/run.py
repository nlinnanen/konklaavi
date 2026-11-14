"""Evaluate page-level detection against EUvsDisinfo article URLs and reliable-outlet negatives.

python -m app.eval [--api URL] [--timeout SECONDS]

Known narratives: the URLs each seed case held out of the import, and mirrors of their text on other
domains. Unseen narratives: the URLs of the test cases, which were held out whole, so nothing was seeded
for them and low recall is expected.

Fetches go through the worker's fetch job, so the worker must be running. Lookups go to the API at
--api, or to the API app in-process.
"""

import argparse
import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from psycopg import Connection

from app.api.main import app
from app.db import connection
from app.eval.metrics import Match, Page, report
from app.jobs import enqueue
from app.jobs.cluster import is_chrome
from app.migrate import migrate
from app.seed.euvsdisinfo import is_test_case, load_cases, split_urls

HERE = Path(__file__).resolve().parent
NEGATIVES_FILE = HERE / "negatives.txt"
RESULTS_FILE = HERE / "results" / "latest.json"
MAX_LOOKUP_HASHES = 500
MATCH_TEXT_CHARS = 300
POLL_SECONDS = 5
# Share of a held-out page's blocks another page must repeat to count as its mirror.
MIRROR_SHARE = 0.5
UNSEEN_REASON = "no narrative was seeded from this case"

log = logging.getLogger(__name__)


def load_negatives(path: Path = NEGATIVES_FILE) -> list[str]:
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


@dataclass(frozen=True)
class Labels:
    """The evaluation's positive URLs, and the URLs fetched as seeds, which are not evaluated."""

    known: dict[str, tuple[str, str]]  # held-out URL -> (case id, id of the narrative seeded from the case)
    unseen: dict[str, str]  # URL of a test case -> case id
    known_cases: int
    unseen_cases: int
    known_overlap: int  # held-out URLs that were also fetched as a seed
    unseen_overlap: int
    seeded: set[str]


def label_urls(conn: Connection, cases: list[dict]) -> Labels:
    """Held-out URLs of the imported seed cases are known positives; test case URLs are unseen positives.

    A URL fetched as a seed, by any case, is in the knowledge base and is evaluated in neither set.
    """
    narratives = dict(conn.execute("SELECT source_url, id FROM narrative WHERE source = 'euvsdisinfo'"))
    imported = [c for c in cases if not is_test_case(c["id"]) and c["url"] in narratives]
    seeded = {url for case in imported for url in split_urls(case)[0]}
    seeded |= {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT payload->>'url' FROM job WHERE kind = 'fetch' AND payload->>'reason' = 'seed'"
        )
    }
    known: dict[str, tuple[str, str]] = {}
    known_overlap: set[str] = set()
    known_cases = 0
    for case in imported:
        held_out = split_urls(case)[1]
        known_cases += bool(held_out)
        for url in held_out:
            if url in seeded:
                known_overlap.add(url)
            else:
                known.setdefault(url, (case["id"], str(narratives[case["url"]])))
    unseen: dict[str, str] = {}
    unseen_overlap: set[str] = set()
    unseen_cases = 0
    for case_id, urls in conn.execute("SELECT case_id, article_urls FROM eval_case ORDER BY case_id"):
        unseen_cases += 1
        for url in urls:
            if url in seeded:
                unseen_overlap.add(url)
            elif url not in known:
                unseen.setdefault(url, case_id)
    return Labels(known, unseen, known_cases, unseen_cases, len(known_overlap), len(unseen_overlap), seeded)


def find_mirrors(conn: Connection, held_out: list[str], labelled: set[str]) -> dict[str, str]:
    """Fetched pages that repeat a held-out page's text on another domain: mirror URL -> held-out URL.

    A mirror is an unlabelled page holding at least MIRROR_SHARE of the held-out page's blocks that are
    not page chrome.
    """
    blocks: dict[str, set[int]] = {}
    shared: dict[tuple[str, str], set[int]] = {}
    for url, fingerprint_id, content, other in conn.execute(
        """
        SELECT h.url, f.id, f.content, other.url
        FROM page h
        JOIN observation o ON o.page_id = h.id
        JOIN fingerprint f ON f.id = o.fingerprint_id
        LEFT JOIN LATERAL (
          SELECT DISTINCT p.url FROM observation o2 JOIN page p ON p.id = o2.page_id
          WHERE o2.fingerprint_id = f.id AND p.domain_id <> h.domain_id AND p.status = 'ok'
        ) other ON true
        WHERE h.url = ANY(%s) AND h.status = 'ok'
        """,
        (held_out,),
    ):
        if is_chrome(content):
            continue
        blocks.setdefault(url, set()).add(fingerprint_id)
        if other is not None and other not in labelled:
            shared.setdefault((url, other), set()).add(fingerprint_id)
    mirrors: dict[str, str] = {}
    for (url, other), ids in sorted(shared.items()):
        if len(ids) >= MIRROR_SHARE * len(blocks[url]):
            mirrors.setdefault(other, url)
    return mirrors


def miss_reason(conn: Connection, url: str, narrative_id: str | None) -> str:
    """Why a fetched positive page matched nothing."""
    if narrative_id is None:
        return UNSEEN_REASON
    blocks = conn.execute(
        "SELECT count(*) FROM fingerprint f JOIN variant v ON v.id = f.variant_id WHERE v.narrative_id = %s",
        (int(narrative_id),),
    ).fetchone()[0]
    if blocks == 0:
        return "the case's narrative has no blocks: its seed pages failed to load or held nothing relevant"
    closest = conn.execute(
        """
        SELECT max(1 - (pf.embedding <=> nf.embedding))
        FROM page p
        JOIN observation o ON o.page_id = p.id
        JOIN fingerprint pf ON pf.id = o.fingerprint_id
        CROSS JOIN (
          SELECT f.embedding FROM fingerprint f JOIN variant v ON v.id = f.variant_id
          WHERE v.narrative_id = %s AND f.embedding IS NOT NULL
        ) nf
        WHERE p.url = %s AND pf.embedding IS NOT NULL
        """,
        (int(narrative_id), url),
    ).fetchone()[0]
    similarity = "no embeddings to compare" if closest is None else f"closest block cosine {closest:.2f}"
    return f"no sentence or 2 bands in common with the case's narrative ({blocks} blocks); {similarity}"


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


def page_status(conn: Connection, url: str) -> str:
    row = conn.execute("SELECT status FROM page WHERE url = %s", (url,)).fetchone()
    if row is None:
        return "not fetched"
    return "no text extracted" if row[0] == "ok" else row[0]


def evaluate(client: httpx.Client, timeout: float) -> dict:
    migrate()
    with connection() as conn:
        labels = label_urls(conn, load_cases())
    negatives = [url for url in load_negatives() if url not in labels.known and url not in labels.unseen]
    fetch_all([*labels.known, *labels.unseen, *negatives], timeout)
    with connection() as conn:
        mirrors = find_mirrors(
            conn, list(labels.known), {*labels.known, *labels.unseen, *negatives, *labels.seeded}
        )

    known = [
        Page(url, True, case_id=case, narrative_id=narrative)
        for url, (case, narrative) in labels.known.items()
    ]
    known += [
        Page(url, True, case_id=labels.known[held][0], narrative_id=labels.known[held][1], mirror_of=held)
        for url, held in mirrors.items()
    ]
    sets = {
        "known": known,
        "unseen": [Page(url, True, case_id=case) for url, case in labels.unseen.items()],
        "negative": [Page(url, False) for url in negatives],
    }
    pages: dict[str, list[Page]] = {name: [] for name in sets}
    unreachable = []
    for name, labelled in sets.items():
        for page in labelled:
            # The in-process API takes a pooled connection too, so none is held across the lookup.
            with connection() as conn:
                blocks = page_blocks(conn, page.url)
                if blocks is None:
                    unreachable.append({"url": page.url, "set": name, "status": page_status(conn, page.url)})
                    continue
            page = replace(page, matches=page_matches(client, blocks))
            if page.positive and not page.flagged:
                with connection() as conn:
                    page = replace(page, reason=miss_reason(conn, page.url, page.narrative_id))
            pages[name].append(page)

    known_report = report(pages["known"] + pages["negative"])
    unseen_report = report(pages["unseen"] + pages["negative"])
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "known": {
            "cases": labels.known_cases,
            "urls": len(labels.known),
            "seed_overlap": labels.known_overlap,
            "mirrors": len(mirrors),
            "pages": len(pages["known"]),
            "flagged_by_own_narrative": sum(p.flagged_by_own_narrative for p in pages["known"]),
            "metrics": known_report["metrics"],
            "false_negatives": known_report["false_negatives"],
            "true_positives": known_report["true_positives"],
        },
        "unseen": {
            "cases": labels.unseen_cases,
            "urls": len(labels.unseen),
            "seed_overlap": labels.unseen_overlap,
            "pages": len(pages["unseen"]),
            "metrics": unseen_report["metrics"],
            "false_negatives": unseen_report["false_negatives"],
            "true_positives": unseen_report["true_positives"],
        },
        "negatives": {
            "urls": len(negatives),
            "pages": len(pages["negative"]),
            "false_positives": known_report["false_positives"],
        },
        "unreachable": unreachable,
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _print_set(title: str, section: dict) -> None:
    m = section["metrics"]
    print(title)
    print(f"{'':10}{'flagged':>10}{'not flagged':>14}")
    print(f"{'positive':10}{m['true_positives']:>10}{m['false_negatives']:>14}")
    print(f"{'negative':10}{m['false_positives']:>10}{m['true_negatives']:>14}")
    print(
        f"precision {_percent(m['precision'])}   recall {_percent(m['recall'])}"
        f"   false-positive rate {_percent(m['false_positive_rate'])}"
    )
    print()


def print_table(results: dict) -> None:
    known, unseen, neg = results["known"], results["unseen"], results["negatives"]
    _print_set(
        f"Known narratives: {known['pages']} pages of {known['urls']} held-out URLs and {known['mirrors']} mirrors"
        f" from {known['cases']} seed cases ({known['seed_overlap']} URLs also fetched as seeds left out);"
        f" {known['flagged_by_own_narrative']} flagged by their own case's narrative",
        known,
    )
    _print_set(
        f"Unseen narratives (held out whole, so low recall is expected): {unseen['pages']} pages of"
        f" {unseen['urls']} URLs from {unseen['cases']} test cases ({unseen['seed_overlap']} URLs also fetched"
        " as seeds left out)",
        unseen,
    )
    print(f"Negatives: {neg['pages']} pages of {neg['urls']} reliable-outlet URLs")
    for page in neg["false_positives"]:
        print(f"\nfalse positive: {page['url']}")
        for match in page["matches"]:
            print(f"  [{match['narrative'] or 'votes'}] {match['text']}")
    for page in known["false_negatives"]:
        print(f"\nfalse negative: {page['url']}\n  {page['reason']}")


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
