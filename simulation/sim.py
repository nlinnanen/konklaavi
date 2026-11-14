"""Simulated voters against the API: honest, lazy, partisan and coordinated brigade users.

uv run python sim.py --api http://localhost:8000 --label unweighted

Votes go to POST /votes and flags are read from POST /lookup. Populations and the flag brigade vote on
fresh synthetic content (one sentence per item, URLs under sim.invalid), so they never touch real data.
The retire brigade votes trustworthy on sentences of real active narratives taken from GET /index, or
given with --narrative-hash; those votes stay in the database.

Each run is stored under its label in the results file, so runs against differently weighted builds of
the API sit side by side.
"""

import argparse
import json
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

VERDICTS = ("misinformation", "manipulative", "misleading", "trustworthy")
RESULTS_FILE = (
    Path(__file__).resolve().parent.parent / "backend" / "app" / "eval" / "results" / "simulation.json"
)
SIM_DOMAIN = "sim.invalid"


@dataclass(frozen=True)
class Item:
    hash: str  # the item's content hash and its only sentence hash
    reliable: bool


class Api:
    """The API over HTTP. `settle` seconds pass before each lookup, for state that jobs update."""

    def __init__(self, client: httpx.Client, settle: float = 0.0):
        self.client = client
        self.settle = settle

    def vote(self, user: str, item: Item, verdict: str) -> None:
        response = self.client.post(
            "/votes",
            json={
                "userId": user,
                "contentHash": item.hash,
                "sentenceHashes": [item.hash],
                "verdict": verdict,
                "url": f"https://{SIM_DOMAIN}/{item.hash}",
                "domain": SIM_DOMAIN,
                "createdAt": datetime.now(UTC).isoformat(),
            },
        )
        response.raise_for_status()

    def flagged(self, hashes: list[str]) -> set[str]:
        if self.settle:
            time.sleep(self.settle)
        response = self.client.post("/lookup", json={"hashes": hashes})
        response.raise_for_status()
        return {r["hash"] for r in response.json()["results"] if r["flagged"]}

    def active_sentences(self) -> list[tuple[str, str]]:
        """(sentence hash, narrative id) for the sentences of active narratives, from GET /index."""
        response = self.client.get("/index")
        response.raise_for_status()
        index = response.json()
        active = {s["narrativeId"] for s in index["signatures"] if s["state"] == "active"}
        return [(s["hash"], s["narrativeId"]) for s in index["sentences"] if s["narrativeId"] in active]


def new_user() -> str:
    return str(uuid.uuid4())


def new_item(reliable: bool) -> Item:
    return Item(f"sim-{uuid.uuid4().hex}", reliable)


# A behaviour gives a user's verdict on an item, or None to abstain.
Behaviour = Callable[[random.Random, Item], str | None]


def honest(rng: random.Random, item: Item) -> str:
    return "trustworthy" if item.reliable else "misinformation"


def lazy(rng: random.Random, item: Item) -> str:
    return rng.choice(VERDICTS)


def partisan(rng: random.Random, item: Item) -> str | None:
    """Flags reliable content and leaves the rest alone."""
    return "misinformation" if item.reliable else None


BEHAVIOURS: dict[str, Behaviour] = {"honest": honest, "lazy": lazy, "partisan": partisan}


def population(api, rng: random.Random, users: dict[str, int], items: int, turnout: float) -> dict:
    """Each user votes on each of `items` reliable and `items` false items with probability `turnout`.

    Returns the share of each kind of item that ends up flagged.
    """
    content = [new_item(True) for _ in range(items)] + [new_item(False) for _ in range(items)]
    votes = 0
    for kind, count in users.items():
        for _ in range(count):
            user = new_user()
            for item in content:
                verdict = BEHAVIOURS[kind](rng, item)
                if verdict is not None and rng.random() < turnout:
                    api.vote(user, item, verdict)
                    votes += 1
    flagged = api.flagged([item.hash for item in content])
    return {
        "users": users,
        "votes": votes,
        "reliable_flagged": sum(i.hash in flagged for i in content if i.reliable) / items,
        "false_flagged": sum(i.hash in flagged for i in content if not i.reliable) / items,
    }


def established_users(api, count: int, history: int) -> list[str]:
    """Accounts that have each voted honestly on `history` reliable items of their own."""
    users = [new_user() for _ in range(count)]
    for user in users:
        for _ in range(history):
            api.vote(user, new_item(True), "trustworthy")
    return users


def brigade_to_flag(api, honest: list[str], brigade: list[str]) -> int | None:
    """Brigade accounts needed to flag a reliable item that the `honest` users called trustworthy.

    The brigade votes misinformation one account at a time; None when all of `brigade` did not suffice.
    """
    item = new_item(True)
    for user in honest:
        api.vote(user, item, "trustworthy")
    for n, user in enumerate(brigade, start=1):
        api.vote(user, item, "misinformation")
        if item.hash in api.flagged([item.hash]):
            return n
    return None


def brigade_to_retire(api, sentence_hash: str, brigade: list[str]) -> int | None:
    """Brigade accounts voting trustworthy needed before a flagged sentence stops being flagged."""
    item = Item(sentence_hash, reliable=True)
    if item.hash not in api.flagged([item.hash]):
        raise ValueError(f"sentence {sentence_hash} is not flagged")
    for n, user in enumerate(brigade, start=1):
        api.vote(user, item, "trustworthy")
        if item.hash not in api.flagged([item.hash]):
            return n
    return None


POPULATIONS = [
    {"honest": 20},
    {"honest": 20, "lazy": 10},
    {"honest": 20, "lazy": 10, "partisan": 5},
]
HONEST_VOTES = [0, 1, 3, 10]


def simulate(
    api,
    seed: int,
    cap: int,
    history: int,
    targets: list[tuple[str, str | None]],
    items: int = 20,
    turnout: float = 0.3,
) -> dict:
    rng = random.Random(seed)
    result: dict = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed": seed,
        "cap": cap,
        "history": history,
        "population": [population(api, rng, users, items, turnout) for users in POPULATIONS],
    }
    # Established accounts: the honest voters who called the item trustworthy, and a brigade with history.
    honest_voters = established_users(api, max(HONEST_VOTES), history)
    established = established_users(api, cap, history)
    brigades: dict[str, Callable[[], list[str]]] = {
        "fresh": lambda: [new_user() for _ in range(cap)],
        "established": lambda: established,
    }
    result["brigade_flag"] = [
        {
            "accounts": accounts,
            "honest_votes": honest_votes,
            "needed": brigade_to_flag(api, honest_voters[:honest_votes], make()),
        }
        for accounts, make in brigades.items()
        for honest_votes in HONEST_VOTES
    ]
    result["brigade_retire"] = [
        {
            "accounts": accounts,
            "narrative_id": narrative_id,
            "sentence_hash": sentence_hash,
            "needed": brigade_to_retire(api, sentence_hash, make()),
        }
        for (accounts, make), (sentence_hash, narrative_id) in zip(brigades.items(), targets, strict=False)
    ]
    return result


def pick_targets(api, given: list[str]) -> list[tuple[str, str | None]]:
    """One sentence each of up to two distinct active narratives: the given hashes, or picks from /index."""
    if given:
        return [(h, None) for h in given[:2]]
    try:
        sentences = api.active_sentences()
    except httpx.HTTPStatusError as error:
        print(f"GET /index answered {error.response.status_code}; the retire brigade does not run")
        return []
    by_narrative: dict[str, str] = {}
    for sentence_hash, narrative_id in random.SystemRandom().sample(sentences, len(sentences)):
        by_narrative.setdefault(narrative_id, sentence_hash)
    return [(h, n) for n, h in list(by_narrative.items())[:2]]


def save(path: Path, label: str, result: dict) -> None:
    """Store the run under its label, keeping the other labels' runs."""
    runs = json.loads(path.read_text())["runs"] if path.is_file() else {}
    runs[label] = result
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"runs": runs}, indent=2) + "\n")


def _needed(n: int | None, cap: int) -> str:
    return f"> {cap}" if n is None else str(n)


def target(row: dict) -> str:
    return (
        f"narrative {row['narrative_id']}" if row["narrative_id"] else f"sentence {row['sentence_hash'][:12]}"
    )


def print_table(label: str, result: dict) -> None:
    cap = result["cap"]
    print(f"run {label!r}\n")
    print(f"{'population':40}{'votes':>7}{'reliable flagged':>18}{'false flagged':>15}")
    for row in result["population"]:
        users = ", ".join(f"{n} {kind}" for kind, n in row["users"].items())
        print(
            f"{users:40}{row['votes']:>7}{100 * row['reliable_flagged']:>17.0f}%{100 * row['false_flagged']:>14.0f}%"
        )
    print(f"\n{'brigade to flag a reliable item':40}{'accounts':>12}{'needed':>8}")
    for row in result["brigade_flag"]:
        print(
            f"{'after ' + str(row['honest_votes']) + ' trustworthy votes':40}{row['accounts']:>12}"
            f"{_needed(row['needed'], cap):>8}"
        )
    print(f"\n{'brigade to retire a narrative':40}{'accounts':>12}{'needed':>8}")
    if not result["brigade_retire"]:
        print("not run: no active narrative sentence")
    for row in result["brigade_retire"]:
        print(f"{target(row):40}{row['accounts']:>12}{_needed(row['needed'], cap):>8}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", default="http://localhost:8000", help="base URL of the API")
    parser.add_argument("--label", default="current", help="name of this run in the results file")
    parser.add_argument("--seed", type=int, default=1, help="seed of the users' random choices")
    parser.add_argument("--cap", type=int, default=30, help="largest brigade tried")
    parser.add_argument("--history", type=int, default=5, help="earlier votes of each established account")
    parser.add_argument("--settle", type=float, default=0.0, help="seconds to wait before each lookup")
    parser.add_argument(
        "--narrative-hash",
        action="append",
        default=[],
        help="sentence hash to retire, instead of /index picks",
    )
    parser.add_argument("--out", type=Path, default=RESULTS_FILE, help="results file")
    args = parser.parse_args()
    with httpx.Client(base_url=args.api, timeout=60) as client:
        api = Api(client, args.settle)
        result = simulate(api, args.seed, args.cap, args.history, pick_targets(api, args.narrative_hash))
    result["api"] = args.api
    save(args.out, args.label, result)
    print_table(args.label, result)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
