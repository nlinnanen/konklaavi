"""Import EUvsDisinfo cases: seed cases become active narratives, test cases go to eval_case.

python -m app.seed.euvsdisinfo [--limit N] [--file PATH]
"""

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from psycopg import Connection

from app.db import connection
from app.jobs import enqueue
from app.migrate import migrate

SEED_FILE = Path(__file__).resolve().parent / "data" / "euvsdisinfo.jsonl"
TEST_PERCENT = 20
# Serialises concurrent imports, which would otherwise create the same narrative twice.
_LOCK_KEY = 0x65757664

log = logging.getLogger(__name__)


def load_cases(path: Path = SEED_FILE, limit: int | None = None) -> list[dict]:
    """The cases in a seed file, in file order, the first `limit` of them when given."""
    with path.open() as f:
        cases = [json.loads(line) for line in f if line.strip()]
    return cases[:limit] if limit is not None else cases


def is_test_case(case_id: str) -> bool:
    """Whether a case belongs to the held-out test set, decided by a hash of its id alone."""
    digest = hashlib.sha256(case_id.encode()).digest()
    return int.from_bytes(digest[:8]) % 100 < TEST_PERCENT


@dataclass(frozen=True)
class ImportResult:
    narratives: int
    fetch_jobs: int
    eval_cases: int


def import_cases(conn: Connection, cases: list[dict]) -> ImportResult:
    """Create a narrative and its fetch jobs per new seed case and an eval_case row per new test case."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
    narratives = fetch_jobs = eval_cases = 0
    for case in cases:
        if is_test_case(case["id"]):
            eval_cases += _insert_eval_case(conn, case)
            continue
        narrative_id = _insert_narrative(conn, case)
        if narrative_id is None:
            continue
        narratives += 1
        for url in case["article_urls"]:
            enqueue(conn, "fetch", {"url": url, "reason": "seed", "narrative_id": narrative_id})
            fetch_jobs += 1
    return ImportResult(narratives, fetch_jobs, eval_cases)


def _insert_narrative(conn: Connection, case: dict) -> int | None:
    """Insert the case's narrative and return its id, or None when it was imported before."""
    exists = conn.execute(
        "SELECT 1 FROM narrative WHERE source = 'euvsdisinfo' AND source_url = %s", (case["url"],)
    ).fetchone()
    if exists:
        return None
    row = conn.execute(
        "INSERT INTO narrative (title, claim, explanation, verdict, source, source_url, state)"
        " VALUES (%s, %s, %s, 'misinformation', 'euvsdisinfo', %s, 'active') RETURNING id",
        (case["title"], case["summary"], case["disproof"], case["url"]),
    ).fetchone()
    return row[0]


def _insert_eval_case(conn: Connection, case: dict) -> int:
    cursor = conn.execute(
        "INSERT INTO eval_case"
        " (case_id, title, claim, explanation, source_url, article_urls, languages, published_on)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (case_id) DO NOTHING",
        (
            case["id"],
            case["title"],
            case["summary"],
            case["disproof"],
            case["url"],
            case["article_urls"],
            case["languages"],
            case.get("date"),
        ),
    )
    return cursor.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", type=Path, default=SEED_FILE, help="seed file, one JSON case per line")
    parser.add_argument("--limit", type=int, help="import only the first N cases of the file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    migrate()
    cases = load_cases(args.file, args.limit)
    with connection() as conn:
        result = import_cases(conn, cases)
    print(
        f"{len(cases)} cases: {result.narratives} new narratives, {result.fetch_jobs} fetch jobs,"
        f" {result.eval_cases} new test cases"
    )


if __name__ == "__main__":
    main()
