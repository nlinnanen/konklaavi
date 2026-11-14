"""Reputation over rounds for honest early reporters, honest late followers and a bot farm.

uv run python crowd.py --api http://localhost:8000 --database postgresql://postgres:postgres@localhost:5432/postgres

Each round, the early reporters report one disinformation case before it settles, the late followers vote
negative on a case that settled long ago, and the bot farm flags one article of a reliable outlet. A report
settles `--delay` rounds after it is made. After every round each user's reputation and weight are read from
GET /me, so the run measures whatever rules the API's build applies.

The run plays two parts of the stack itself, so it needs no LLM key and no waiting for jobs: it files each
report under a report narrative, as the analyze job does, and confirms that narrative against the case it
reports, as settlement does. The cases are active EUvsDisinfo and fact-checker narratives; a report carries
the case's title as its text under a URL on sim.invalid, and the late followers vote on the case's own
sentences. The bots flag URLs from the evaluation's reliable-outlet negatives. Everything the run writes
stays in the database.

The run is stored under the "crowd" key of the results file.
"""

import argparse
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg

from sim import RESULTS_FILE, SIM_DOMAIN

NEGATIVES = Path(__file__).resolve().parent.parent / "backend" / "app" / "eval" / "negatives.txt"
GROUPS = ("early", "late", "bots")


@dataclass(frozen=True)
class Case:
    narrative_id: int
    title: str
    sentence_hash: str  # one sentence of the case, which the late followers vote on


class Stack:
    """Votes and /me over HTTP; the analyze and settlement steps as writes to the database."""

    def __init__(self, client: httpx.Client, conn: psycopg.Connection):
        self.client = client
        self.conn = conn

    def vote(self, user: str, content_hash: str, url: str) -> None:
        response = self.client.post(
            "/votes",
            json={
                "userId": user,
                "contentHash": content_hash,
                "sentenceHashes": [content_hash],
                "verdict": "misinformation",
                "url": url,
                "domain": httpx.URL(url).host,
                "createdAt": datetime.now(UTC).isoformat(),
            },
        )
        response.raise_for_status()

    def me(self, user: str) -> dict:
        response = self.client.get("/me", params={"userId": user})
        response.raise_for_status()
        return response.json()

    def cases(self, count: int) -> list[Case]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT ON (n.id) n.id, n.title, s.hash
            FROM narrative n
            JOIN variant va ON va.narrative_id = n.id
            JOIN fingerprint f ON f.variant_id = va.id
            JOIN sentence s ON s.fingerprint_id = f.id
            WHERE n.source IN ('euvsdisinfo', 'factcheck') AND n.state = 'active'
            ORDER BY n.id, s.hash
            LIMIT %s
            """,
            (count,),
        ).fetchall()
        return [Case(*row) for row in rows]

    def file_report(self, case: Case) -> tuple[int, str]:
        """A report narrative under review holding one block with the case's title as its text.

        Returns the narrative id and the block's hash, which is also its only sentence hash.
        """
        content_hash = f"sim-{uuid.uuid4().hex}"
        with self.conn.transaction():
            narrative = self.conn.execute(
                "INSERT INTO narrative (title, claim, source, state) VALUES (%s, %s, 'report', 'under_review')"
                " RETURNING id",
                (case.title, case.title),
            ).fetchone()[0]
            variant = self.conn.execute(
                "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative,)
            ).fetchone()[0]
            fingerprint = self.conn.execute(
                "INSERT INTO fingerprint (content_hash, content, variant_id) VALUES (%s, %s, %s) RETURNING id",
                (content_hash, case.title, variant),
            ).fetchone()[0]
            self.conn.execute(
                "INSERT INTO sentence (hash, fingerprint_id) VALUES (%s, %s)", (content_hash, fingerprint)
            )
        return narrative, content_hash

    def attach(self, narrative_id: int, content_hash: str) -> None:
        self.conn.execute(
            "UPDATE vote SET narrative_id = %s WHERE content_hash = %s", (narrative_id, content_hash)
        )

    def confirm(self, narrative_id: int, case: Case) -> None:
        self.conn.execute(
            "UPDATE narrative SET state = 'active', settled_by = %s, settled_at = now(), updated_at = now()"
            " WHERE id = %s AND settled_at IS NULL",
            (case.narrative_id, narrative_id),
        )


def reliable_urls(count: int) -> list[str]:
    """The first negative of each of `count` outlets."""
    by_host: dict[str, str] = {}
    for line in NEGATIVES.read_text().splitlines():
        if line and not line.startswith("#"):
            by_host.setdefault(httpx.URL(line).host, line)
    return list(by_host.values())[:count]


def new_user() -> str:
    return str(uuid.uuid4())


def snapshot(stack, round_: int, users: dict[str, list[str]]) -> dict:
    """Each group's mean reputation, weight, agreements and disagreements after `round_` rounds."""
    groups = {}
    for group, members in users.items():
        mes = [stack.me(user) for user in members]
        groups[group] = {
            key: sum(me[key] for me in mes) / len(mes)
            for key in ("reputation", "weight", "agreed", "disagreed")
        }
    return {"round": round_, "groups": groups}


def simulate(stack, cases: list[Case], urls: list[str], sizes: dict[str, int], delay: int) -> dict:
    """One round per case: a report of it, a late vote on it and a bot flag on the round's reliable URL."""
    users = {group: [new_user() for _ in range(sizes[group])] for group in GROUPS}
    rounds = [snapshot(stack, 0, users)]
    settlements = []
    pending: list[tuple[int, int, Case]] = []
    for round_, (case, url) in enumerate(zip(cases, urls, strict=True), start=1):
        report, content_hash = stack.file_report(case)
        for user in users["early"]:
            stack.vote(user, content_hash, f"https://{SIM_DOMAIN}/{content_hash}")
        stack.attach(report, content_hash)
        pending.append((round_ + delay, report, case))
        for user in users["late"]:
            stack.vote(user, case.sentence_hash, f"https://{SIM_DOMAIN}/{case.sentence_hash}")
        flagged = f"sim-{uuid.uuid4().hex}"
        for user in users["bots"]:
            stack.vote(user, flagged, url)
        for _, settled, settled_case in [p for p in pending if p[0] == round_]:
            stack.confirm(settled, settled_case)
            settlements.append({"round": round_, "report": settled, "case": settled_case.narrative_id})
        pending = [p for p in pending if p[0] != round_]
        rounds.append(snapshot(stack, round_, users))
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "groups": sizes,
        "delay": delay,
        "rounds": rounds,
        "settlements": settlements,
        "bots_below_honest_after": bots_below_honest_after(rounds),
    }


def bots_below_honest_after(rounds: list[dict]) -> int | None:
    """Reports per bot after which the bots' mean weight stays below both honest groups', or None."""
    below = [
        row["groups"]["bots"]["weight"]
        < min(row["groups"]["early"]["weight"], row["groups"]["late"]["weight"])
        for row in rounds
    ]
    if not below[-1]:
        return None
    first = len(below)
    while first > 0 and below[first - 1]:
        first -= 1
    return rounds[first]["round"]


def save(path: Path, result: dict) -> None:
    results = json.loads(path.read_text()) if path.is_file() else {}
    results["crowd"] = result
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2) + "\n")


def print_table(result: dict) -> None:
    sizes = ", ".join(f"{n} {group}" for group, n in result["groups"].items())
    print(f"{sizes}; a report settles {result['delay']} round(s) after it is made\n")
    print(f"{'round':>5}" + "".join(f"{group + ' rep':>12}{group + ' weight':>14}" for group in GROUPS))
    for row in result["rounds"]:
        print(
            f"{row['round']:>5}"
            + "".join(
                f"{row['groups'][g]['reputation']:>12.3f}{row['groups'][g]['weight']:>14.3f}" for g in GROUPS
            )
        )
    after = result["bots_below_honest_after"]
    print(
        "\nbots' weight never stays below the honest groups'"
        if after is None
        else f"\nbots' weight stays below the honest groups' after {after} report(s) each"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", default="http://localhost:8000", help="base URL of the API")
    parser.add_argument(
        "--database", default="postgresql://postgres:postgres@localhost:5432/postgres", help="database URL"
    )
    parser.add_argument(
        "--rounds", type=int, default=6, help="rounds; each uses one case and one reliable URL"
    )
    parser.add_argument("--delay", type=int, default=1, help="rounds from a report to its settlement")
    parser.add_argument("--early", type=int, default=5, help="honest early reporters")
    parser.add_argument("--late", type=int, default=5, help="honest late followers")
    parser.add_argument("--bots", type=int, default=10, help="accounts of the bot farm")
    parser.add_argument("--out", type=Path, default=RESULTS_FILE, help="results file")
    args = parser.parse_args()
    sizes = {"early": args.early, "late": args.late, "bots": args.bots}
    with (
        httpx.Client(base_url=args.api, timeout=60) as client,
        psycopg.connect(args.database, autocommit=True) as conn,
    ):
        stack = Stack(client, conn)
        cases = stack.cases(args.rounds)
        if len(cases) < args.rounds:
            raise SystemExit(f"{len(cases)} active EUvsDisinfo or fact-checker cases, {args.rounds} needed")
        result = simulate(stack, cases, reliable_urls(args.rounds), sizes, args.delay)
    result["api"] = args.api
    save(args.out, result)
    print_table(result)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
