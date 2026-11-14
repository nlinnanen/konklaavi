"""The bots' loop: each persona writes a note on a recent article and rates others' notes at its own pace,
until the time or the credit budget runs out."""

import logging
import random
import re
import signal
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from crowd.api import Api, ApiError
from crowd.behaviours import RATE_TOOL, WRITE_TOOL, rate_prompt, write_prompt
from crowd.config import Config, Persona
from crowd.llm import Llm
from crowd.state import State
from crowd.web import Web

ARTICLE_CHARS = 6000
RATE_BATCH = 3
MAX_BODY = 1000
SEEN_ARTICLES = 300

log = logging.getLogger(__name__)


@dataclass
class Bot:
    persona: Persona
    record: dict
    next_write: float
    next_rate: float
    writes: deque = field(default_factory=deque)
    rates: deque = field(default_factory=deque)

    @property
    def install(self) -> str:
        return self.record["install"]


def within_hour(times: deque, now: float) -> int:
    while times and now - times[0] > 3600:
        times.popleft()
    return len(times)


def find_quote(quote: str, text: str) -> str | None:
    """The quote as the article spells it, matching across differences in whitespace; None when absent."""
    words = quote.split()
    if not words:
        return None
    match = re.search(r"\s+".join(re.escape(w) for w in words), text)
    return match.group(0) if match else None


class Runner:
    def __init__(self, config: Config, api: Api, state: State, llm: Llm, web: Web, log_path: Path):
        self.config, self.api, self.state, self.llm, self.web = config, api, state, llm, web
        self.credits = 0.0
        self.stopping = False
        self.actions = open(log_path, "a", buffering=1)  # noqa: SIM115 - open for the whole run

    def action(self, bot: Bot, kind: str, result: str, detail: str = "") -> None:
        line = f"{datetime.now(UTC).isoformat(timespec='seconds')} {bot.persona.name} {kind} {result}"
        line += f" credits={self.credits:.3f}" + (f" {detail}" if detail else "")
        self.actions.write(line + "\n")
        print(line, flush=True)

    def run(self, minutes: float, budget: float) -> None:
        deadline = time.monotonic() + minutes * 60
        previous = signal.signal(signal.SIGINT, self._interrupt)
        try:
            bots = self._start()
            while not self.stopping:
                now = time.monotonic()
                if now >= deadline:
                    print("time is up", flush=True)
                    break
                if self.credits >= budget:
                    print(f"budget reached: {self.credits:.3f} of {budget} credits", flush=True)
                    break
                due = min(bots, key=lambda b: min(b.next_write, b.next_rate))
                wait = min(due.next_write, due.next_rate, deadline) - now
                if wait > 0:
                    time.sleep(min(wait, 1.0))
                    continue
                if due.next_write <= due.next_rate:
                    self._write(due)
                    due.next_write = time.monotonic() + self._interval(due.persona.writes_per_hour)
                else:
                    self._rate(due)
                    due.next_rate = time.monotonic() + self._interval(due.persona.rates_per_hour / RATE_BATCH)
                self.state.save()
        finally:
            signal.signal(signal.SIGINT, previous)
            self.state.save()
            print(f"stopped; {self.credits:.3f} credits used this run", flush=True)

    def _interrupt(self, *_):
        if self.stopping:
            raise KeyboardInterrupt
        print("stopping after the current action; Ctrl-C again to quit now", flush=True)
        self.stopping = True

    @staticmethod
    def _interval(per_hour: float) -> float:
        return 3600 / per_hour * random.uniform(0.7, 1.3) if per_hour > 0 else float("inf")

    def _start(self) -> list[Bot]:
        bots = []
        now = time.monotonic()
        for persona in self.config.personas:
            record = self.state.persona(persona, self.config.domain)
            if persona.logged_in and not self._signed_in(persona, record):
                continue
            bots.append(
                Bot(
                    persona,
                    record,
                    next_write=now + random.uniform(0, 0.5) * self._interval(persona.writes_per_hour),
                    next_rate=now
                    + random.uniform(0.3, 1.0) * self._interval(persona.rates_per_hour / RATE_BATCH),
                )
            )
        self.state.save()
        if not bots:
            raise SystemExit("no persona could start")
        return bots

    def _signed_in(self, persona: Persona, record: dict) -> bool:
        try:
            me = self.api.me(record["install"])
            if not me["verified"] or me["email"] != record["email"]:
                self.api.sign_in(record["install"], record["email"], self.state.password(record))
            return True
        except ApiError as err:
            print(f"{persona.name}: sign-in failed ({err}); it sits this run out", flush=True)
            return False

    def _charge(self, credits: float) -> None:
        self.credits += credits
        self.state.data["credits"] = self.state.data.get("credits", 0.0) + credits

    def _write(self, bot: Bot) -> None:
        if within_hour(bot.writes, time.monotonic()) >= bot.persona.writes_per_hour:
            return
        bot.writes.append(time.monotonic())
        site = random.choice(bot.persona.sites)
        seen = set(bot.record["articles"])
        article = self.web.article(self.config.sites[site], seen)
        known = set(bot.record["articles"])
        bot.record["articles"] = (bot.record["articles"] + [u for u in seen if u not in known])[
            -SEEN_ARTICLES:
        ]
        if article is None:
            self.action(bot, "write", "no-article", f"site={site}")
            return
        call = self.llm.call(
            write_prompt(bot.persona.behaviour),
            {
                "site": site,
                "url": article.url,
                "title": article.title,
                "article": article.text[:ARTICLE_CHARS],
            },
            WRITE_TOOL,
        )
        self._charge(call.credits)
        note = call.input or {}
        if note.get("decision") != "write":
            self.action(bot, "write", "skip" if call.input else "no-tool-call", f"url={article.url}")
            return
        passage = find_quote(note.get("passage", ""), article.text)
        if passage is None or note.get("classification") not in ("misleading", "not_misleading"):
            self.action(bot, "write", "bad-quote", f"url={article.url}")
            return
        sources = [s for s in note.get("sources") or [] if re.match(r"^https?://\S+$", s)][:10]
        try:
            created = self.api.write_note(
                bot.install, passage, article.url, note["classification"], note["body"][:MAX_BODY], sources
            )
        except ApiError as err:
            self.action(bot, "write", "rejected", f"url={article.url} {err}")
            return
        bot.record["notes"].append(
            {
                "id": created["noteId"],
                "narrativeId": created["narrativeId"],
                "url": article.url,
                "classification": note["classification"],
                "passage": passage,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        self.action(
            bot, "write", "note", f"id={created['noteId']} {note['classification']} url={article.url}"
        )

    def _rate(self, bot: Bot) -> None:
        room = int(bot.persona.rates_per_hour) - within_hour(bot.rates, time.monotonic())
        if room <= 0:
            return
        own = {n["id"] for n in bot.record["notes"]}
        rated = {r["note"] for r in bot.record["ratings"]}
        try:
            notes = [n for n in self.api.recent_notes(100) if n["id"] not in own | rated]
        except ApiError as err:
            self.action(bot, "rate", "error", str(err))
            return
        if not notes:
            self.action(bot, "rate", "nothing-to-rate")
            return
        if bot.persona.behaviour == "brigade":
            ratings = self._brigade_ratings(bot, notes, min(room, RATE_BATCH))
        else:
            batch = random.sample(notes, min(room, RATE_BATCH, len(notes)))
            call = self.llm.call(
                rate_prompt(bot.persona.behaviour),
                {
                    "notes": [
                        {k: n[k] for k in ("id", "passage", "url", "classification", "body", "sources")}
                        for n in batch
                    ]
                },
                RATE_TOOL,
                max_tokens=400,
            )
            self._charge(call.credits)
            ids = {n["id"] for n in batch}
            ratings = [
                (r["id"], r["helpfulness"])
                for r in (call.input or {}).get("ratings", [])
                if r.get("id") in ids and r.get("helpfulness") in ("helpful", "somewhat", "not_helpful")
            ]
            if not ratings:
                self.action(bot, "rate", "skip", f"notes={','.join(sorted(ids))}")
        for note_id, helpfulness in ratings:
            try:
                self.api.rate(bot.install, note_id, helpfulness)
            except ApiError as err:
                self.action(bot, "rate", "rejected", f"note={note_id} {err}")
                continue
            bot.rates.append(time.monotonic())
            bot.record["ratings"].append(
                {
                    "note": note_id,
                    "helpfulness": helpfulness,
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
            self.action(bot, "rate", helpfulness, f"note={note_id}")

    def _brigade_ratings(self, bot: Bot, notes: list[dict], n: int) -> list[tuple[str, str]]:
        """Members' notes helpful first, then everyone else's not helpful: the group's coordination."""
        members = {
            note["id"]
            for name, record in self.state.data["personas"].items()
            if record.get("behaviour") == "brigade" and name != bot.persona.name
            for note in record["notes"]
        }
        ours = [note for note in notes if note["id"] in members]
        theirs = [note for note in notes if note["id"] not in members]
        random.shuffle(theirs)
        picked = (ours + theirs)[:n]
        return [(note["id"], "helpful" if note["id"] in members else "not_helpful") for note in picked]
