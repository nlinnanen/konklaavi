"""uv run python -m crowd {run,report,remove} --config crowd.yaml --api https://.../api"""

import argparse
import logging
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from crowd import config as crowd_config
from crowd.api import Api, ApiError
from crowd.behaviours import GOOD
from crowd.state import State, path_for


def run(args, config, state: State) -> None:
    from crowd.llm import Llm
    from crowd.runner import Runner
    from crowd.web import Web

    host = urlsplit(args.api).netloc.replace(":", "_")
    log_path = args.config.parent / f"crowd-{host}.log"
    Runner(config, Api(args.api), state, Llm(args.model), Web(), log_path).run(
        args.minutes, args.budget_credits
    )


def report(args, config, state: State) -> None:
    api = Api(args.api)
    reputations: dict[bool, list[float]] = {True: [], False: []}
    print(f"{'persona':<16} {'behaviour':<21} {'in':<3} notes  statuses{'':<36} ratings agreed  rep")
    for name, record in state.data["personas"].items():
        try:
            me = api.me(record["install"])
        except ApiError as err:
            print(f"{name:<16} /me failed: {err}")
            continue
        statuses = Counter(
            n["status"] if n["moderation"] == "visible" else n["moderation"] for n in me["notes"]
        )
        summary = " ".join(f"{k}={v}" for k, v in sorted(statuses.items())) or "-"
        print(
            f"{name:<16} {record['behaviour']:<21} {'y' if record['logged_in'] else 'n':<3} "
            f"{len(record['notes']):>5}  {summary:<44} {len(record['ratings']):>7} "
            f"{me['ratings']['agreed']:>6} {me['reputation']:.2f}"
        )
        if me["verified"]:
            reputations[record["behaviour"] in GOOD].append(me["reputation"])
    for good, values in reputations.items():
        if values:
            label = "good" if good else "bad"
            print(
                f"mean reputation of signed-in {label} personas: {sum(values) / len(values):.3f} ({len(values)})"
            )
    print(f"credits used across runs: {state.data.get('credits', 0):.3f}")


def remove(args, config, state: State) -> None:
    anonymous = [r["install"] for r in state.data["personas"].values() if not r["logged_in"]]
    command = f"python -m app.demo.remove_bots --domain {config.domain}"
    command += "".join(f" --install {install}" for install in anonymous)
    print("Run on the server, in the directory of its compose project:")
    print(f"docker compose exec api {command}")
    print(f"Then delete {state.path} to start the bots afresh.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="crowd", description="LLM crowd bots for HashTrust")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "report", "remove"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", type=Path, required=True)
        sub.add_argument("--api", required=True, help="API base URL, such as http://localhost:8000")
        if name == "run":
            sub.add_argument("--minutes", type=float, required=True)
            sub.add_argument("--budget-credits", type=float, required=True)
            sub.add_argument("--model", default="claude-sonnet-5.5")
    args = parser.parse_args()
    args.config = args.config.resolve()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    config = crowd_config.load(args.config)
    state = State(path_for(args.config, args.api), args.api)
    {"run": run, "report": report, "remove": remove}[args.command](args, config, state)


if __name__ == "__main__":
    main()
