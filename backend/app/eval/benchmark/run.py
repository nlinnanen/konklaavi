"""Measure narrative recognition against EUvsDisinfo article URLs and reliable-outlet negatives.

python -m app.eval.benchmark run [--timeout SECONDS] [--out PATH]

Known narratives: the URLs each seed case held out of the import, and mirrors of their text on other
domains. Unseen narratives: the URLs of the test cases, which were held out whole, so nothing was seeded
for them and low recall is expected.

A page is recognised when /lookup matches one of its blocks or sentences to a narrative. Narrative-level
matching recognises a block of MIN_WORDS or more words, with no exact or variant match, whose cosine to one
of a narrative's vectors reaches a threshold. The evaluation sweeps THRESHOLDS and
picks the one with the best known recall within MAX_FALSE_POSITIVE_RATE on the negatives.

Fetches go through the fetch job, so a worker on the benchmark database must be running. Lookups go to
the API app in-process, which uses the benchmark database too.
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
import numpy as np
from fastapi.testclient import TestClient
from psycopg import Connection

from app.api.main import app
from app.config import settings
from app.db import connection
from app.eval.benchmark.cases import is_test_case, load_cases, split_urls
from app.eval.benchmark.database import require_benchmark_database
from app.eval.benchmark.metrics import Match, Page, at_threshold, best_threshold, entry, report, sweep
from app.jobs import enqueue
from app.jobs.cluster import is_chrome

HERE = Path(__file__).resolve().parent
# Shared with app/reliable.py.
NEGATIVES_FILE = HERE.parent / "negatives.txt"
RESULTS_FILE = HERE / "results" / "latest.json"
MAX_LOOKUP_HASHES = 500
MATCH_TEXT_CHARS = 300
POLL_SECONDS = 5
# Share of a held-out page's blocks another page must repeat to count as its mirror.
MIRROR_SHARE = 0.5
UNSEEN_REASON = "no narrative was seeded from this case"
MIN_WORDS = 8
THRESHOLDS = [round(0.7 + 0.025 * i, 3) for i in range(11)]
MAX_FALSE_POSITIVE_RATE = 0.01

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
    narratives = dict(conn.execute("SELECT source_url, id FROM narrative WHERE source = 'note'"))
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
    """The fetched page's blocks as {hash, content, embedding, sentences, bands}, or None when it was not
    fetched."""
    rows = conn.execute(
        """
        SELECT DISTINCT f.id, f.content_hash, f.content, f.embedding::real[]
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
        {
            "hash": content_hash,
            "content": content,
            "embedding": embedding,
            "sentences": sentences.get(id, []),
            "bands": bands.get(id),
        }
        for id, content_hash, content, embedding in rows
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


def page_matches(client: httpx.Client, blocks: list[dict]) -> tuple[list[Match], set[str]]:
    """One match per matched block and narrative, carrying the block's text, and the matched blocks' hashes."""
    block_of = {block["hash"]: block for block in blocks}
    block_of.update({h: block for block in blocks for h in block["sentences"]})
    matches = []
    flagged = set()
    for body in lookup_requests(blocks):
        response = client.post("/lookup", json=body)
        response.raise_for_status()
        for result in response.json()["results"]:
            narrative = result.get("narrative")
            if not narrative:
                continue
            block = block_of[result["hash"]]
            flagged.add(block["hash"])
            matches.append(
                Match(
                    text=block["content"][:MATCH_TEXT_CHARS],
                    narrative_id=narrative["narrativeId"],
                    narrative=narrative["title"],
                )
            )
    return list(dict.fromkeys(matches)), flagged


@dataclass(frozen=True)
class NarrativeVectors:
    narratives: list[tuple[str, str]]  # (id, title) per row of vectors
    vectors: np.ndarray  # unit vectors, one per row


def narrative_vectors(conn: Connection) -> NarrativeVectors:
    """The vectors of the narratives /index ships."""
    rows = conn.execute(
        """
        SELECT n.id, n.title, nv.embedding::real[]
        FROM narrative_vector nv JOIN narrative n ON n.id = nv.narrative_id
        WHERE n.source = 'note'
        """
    ).fetchall()
    return NarrativeVectors(
        [(str(id), title) for id, title, _ in rows],
        np.array([embedding for _, _, embedding in rows], dtype=np.float32).reshape(len(rows), -1),
    )


def near_matches(blocks: list[dict], flagged: set[str], narratives: NarrativeVectors) -> list[Match]:
    """For each block of MIN_WORDS or more words with an embedding and no exact or variant match, its
    closest narrative and the cosine to that narrative's closest vector."""
    eligible = [
        b
        for b in blocks
        if b["hash"] not in flagged and b["embedding"] is not None and len(b["content"].split()) >= MIN_WORDS
    ]
    if not eligible or not len(narratives.vectors):
        return []
    cosines = np.array([b["embedding"] for b in eligible], dtype=np.float32) @ narratives.vectors.T
    closest = cosines.argmax(axis=1)
    return [
        Match(
            text=block["content"][:MATCH_TEXT_CHARS],
            narrative_id=narratives.narratives[j][0],
            narrative=narratives.narratives[j][1],
            cosine=round(float(cosines[i, j]), 4),
        )
        for i, (block, j) in enumerate(zip(eligible, closest, strict=True))
    ]


def page_status(conn: Connection, url: str) -> str:
    row = conn.execute("SELECT status FROM page WHERE url = %s", (url,)).fetchone()
    if row is None:
        return "not fetched"
    return "no text extracted" if row[0] == "ok" else row[0]


def evaluate(client: httpx.Client, timeout: float) -> dict:
    with connection() as conn:
        labels = label_urls(conn, load_cases())
        narratives = narrative_vectors(conn)
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
            matches, flagged = page_matches(client, blocks)
            page = replace(page, matches=matches, near=near_matches(blocks, flagged, narratives))
            if page.positive and not page.flagged:
                with connection() as conn:
                    page = replace(page, reason=miss_reason(conn, page.url, page.narrative_id))
            pages[name].append(page)

    known_report = report(pages["known"] + pages["negative"])
    unseen_report = report(pages["unseen"] + pages["negative"])
    rows = sweep({"known": pages["known"], "unseen": pages["unseen"]}, pages["negative"], THRESHOLDS)
    best = best_threshold(rows, "known_recall", MAX_FALSE_POSITIVE_RATE)
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
        "narrative": {
            "narratives": len({id for id, _ in narratives.narratives}),
            "vectors": len(narratives.narratives),
            "min_words": MIN_WORDS,
            "max_false_positive_rate": MAX_FALSE_POSITIVE_RATE,
            "sweep": rows,
            "best_threshold": best,
            "false_positives": narrative_only(pages["negative"], best),
            "true_positives": narrative_only(pages["known"] + pages["unseen"], best),
        },
        "unreachable": unreachable,
    }


def narrative_only(pages: list[Page], threshold: float | None) -> list[dict]:
    """The pages that only narrative-level matches at the threshold flag."""
    if threshold is None:
        return []
    flagged = (at_threshold(p, threshold) for p in pages if not p.flagged)
    return [entry(p) for p in flagged if p.flagged]


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
            print(f"  [{match['narrative']}] {match['text']}")
    for page in known["false_negatives"]:
        print(f"\nfalse negative: {page['url']}\n  {page['reason']}")
    print_sweep(results["narrative"])


def print_sweep(section: dict) -> None:
    print(
        f"\nNarrative-level matching: {section['vectors']} vectors of {section['narratives']}"
        f" narratives, blocks of {section['min_words']}+ words"
    )
    print(f"{'threshold':>10}{'FPR':>8}{'known P':>10}{'known R':>10}{'unseen P':>10}{'unseen R':>10}")
    for r in section["sweep"]:
        print(
            f"{r['threshold']:>10.3f}{_percent(r['false_positive_rate']):>8}"
            f"{_percent(r['known_precision']):>10}{_percent(r['known_recall']):>10}"
            f"{_percent(r['unseen_precision']):>10}{_percent(r['unseen_recall']):>10}"
        )
    limit = _percent(section["max_false_positive_rate"])
    if section["best_threshold"] is None:
        print(f"no threshold keeps the false-positive rate at or below {limit}")
    else:
        print(f"best known recall at a false-positive rate of at most {limit}: {section['best_threshold']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait for fetch jobs")
    parser.add_argument("--out", type=Path, default=RESULTS_FILE, help="where to write the JSON results")
    args = parser.parse_args()
    require_benchmark_database(settings.database_url)
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.setLevel(logging.INFO)
    with TestClient(app) as client:
        results = evaluate(client, args.timeout)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print_table(results)
    print(f"\nwrote {args.out}")
