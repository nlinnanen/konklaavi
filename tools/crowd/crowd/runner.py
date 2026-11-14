"""The bots' loop: each persona writes a note on a recent article and rates others' notes at its own pace,
until the time or the credit budget runs out.

A scheduler on the main thread hands due actions to a pool of workers. A persona has at most one action in
flight, so its own actions stay sequential and its hourly caps hold, while different personas run at once.
"""

import logging
import os
import random
import re
import signal
import threading
import time
import traceback
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from crowd.api import Api, ApiError
from crowd.behaviours import (
    BAD,
    OPINION_TOOL,
    RATE_TOOL,
    WRITE_TOOL,
    opinion_prompt,
    rate_prompt,
    write_prompt,
)
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
    busy: bool = False

    @property
    def due(self) -> float:
        return min(self.next_write, self.next_rate)

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
    def __init__(
        self, config: Config, api: Api, state: State, llm: Llm, web: Web, log_path: Path, workers: int = 16
    ):
        self.config, self.api, self.state, self.llm, self.web = config, api, state, llm, web
        self.workers = workers
        self.credits = 0.0
        self.stopping = False
        self.output = threading.RLock()  # the SIGINT handler prints on the main thread
        self.actions = open(log_path, "a", buffering=1)  # noqa: SIM115 - open for the whole run

    def action(self, bot: Bot, kind: str, result: str, detail: str = "") -> None:
        with self.output:
            line = f"{datetime.now(UTC).isoformat(timespec='seconds')} {bot.persona.name} {kind} {result}"
            line += f" credits={self.credits:.3f}" + (f" {detail}" if detail else "")
            self.actions.write(line + "\n")
            print(line, flush=True)

    def say(self, message: str) -> None:
        with self.output:
            print(message, flush=True)

    def run(self, minutes: float, budget: float) -> None:
        deadline = time.monotonic() + minutes * 60
        previous = signal.signal(signal.SIGINT, self._interrupt)
        pool = ThreadPoolExecutor(self.workers, thread_name_prefix="crowd")
        running: dict[Future, Bot] = {}
        try:
            bots = self._start()
            while not self.stopping:
                now = time.monotonic()
                if now >= deadline:
                    self.say("time is up")
                    break
                if self.credits >= budget:
                    self.say(f"budget reached: {self.credits:.3f} of {budget} credits")
                    break
                idle = sorted((b for b in bots if not b.busy), key=lambda b: b.due)
                for bot in idle[: self.workers - len(running)]:
                    if bot.due > now:
                        break
                    bot.busy = True
                    running[pool.submit(self._step, bot)] = bot
                # Sleep until the next idle bot is due, or an action finishes and frees its bot and worker.
                waiting = [b.due for b in bots if not b.busy] if len(running) < self.workers else []
                pause = min(max(min(waiting + [deadline]) - time.monotonic(), 0.05), 1.0)
                if running:
                    done, _ = wait(running, timeout=pause, return_when=FIRST_COMPLETED)
                    for future in done:
                        running.pop(future).busy = False
                else:
                    time.sleep(pause)
            if running:
                self.say(f"waiting for {len(running)} actions in flight")
            pool.shutdown(wait=True)
        except KeyboardInterrupt:
            self.state.save()
            self.say(f"quit with actions in flight; {self.credits:.3f} credits used this run")
            os._exit(130)
        finally:
            signal.signal(signal.SIGINT, previous)
            self.state.save()
        self.say(f"stopped; {self.credits:.3f} credits used this run")

    def _interrupt(self, *_):
        if self.stopping:
            raise KeyboardInterrupt
        self.say("stopping after the actions in flight; Ctrl-C again to quit now")
        self.stopping = True

    def _step(self, bot: Bot) -> None:
        """The bot's next due action, on a worker."""
        writing = bot.next_write <= bot.next_rate
        try:
            if writing:
                self._write(bot)
            else:
                self._rate(bot)
        except Exception as err:  # noqa: BLE001 - one failed action must not end the run
            self.action(bot, "write" if writing else "rate", "error", f"{type(err).__name__}: {err}")
            traceback.print_exc()
        if writing:
            bot.next_write = time.monotonic() + self._interval(bot.persona.writes_per_hour)
        else:
            bot.next_rate = time.monotonic() + self._interval(bot.persona.rates_per_hour / RATE_BATCH)
        self.state.save()

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
            self.say(f"{persona.name}: sign-in failed ({err}); it sits this run out")
            return False

    def _charge(self, credits: float) -> None:
        with self.state.lock:
            self.credits += credits
            self.state.data["credits"] = self.state.data.get("credits", 0.0) + credits

    def _write(self, bot: Bot) -> None:
        if within_hour(bot.writes, time.monotonic()) >= bot.persona.writes_per_hour:
            return
        bot.writes.append(time.monotonic())
        site = random.choice(bot.persona.sites)
        seen = set(bot.record["articles"])
        article = self.web.article(self.config.sites[site].feeds, seen)
        with self.state.lock:
            known = set(bot.record["articles"])
            bot.record["articles"] = (bot.record["articles"] + [u for u in seen if u not in known])[
                -SEEN_ARTICLES:
            ]
        if article is None:
            self.action(bot, "write", "no-article", f"site={site}")
            return
        user = {
            "site": site,
            "url": article.url,
            "title": article.title,
            "article": article.text[:ARTICLE_CHARS],
        }
        if bot.persona.behaviour in BAD:
            # The bad part is the runner's: an accurate outlet's article is labelled misleading, another's
            # not misleading, and the model only writes the one-sided opinion attached.
            accurate = self.config.sites[site].accurate
            call = self.llm.call(opinion_prompt(bot.persona.behaviour, accurate), user, OPINION_TOOL)
            self._charge(call.credits)
            if not call.input:
                self.action(bot, "write", "no-tool-call", f"url={article.url}")
                return
            note = dict(call.input, classification="misleading" if accurate else "not_misleading")
        else:
            call = self.llm.call(write_prompt(bot.persona.behaviour), user, WRITE_TOOL)
            self._charge(call.credits)
            note = call.input or {}
            if note.get("decision") != "write":
                self.action(bot, "write", "skip" if call.input else "no-tool-call", f"url={article.url}")
                return
        passage = find_quote(note.get("passage", ""), article.text)
        if (
            passage is None
            or note.get("classification") not in ("misleading", "not_misleading")
            or not note.get("body")
        ):
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
        with self.state.lock:
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
        with self.state.lock:
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
        elif bot.persona.behaviour == "troll":
            ratings = self._troll_ratings(bot, notes, min(room, RATE_BATCH))
            if not ratings:
                self.action(bot, "rate", "nothing-to-rate")
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
            with self.state.lock:
                bot.record["ratings"].append(
                    {
                        "note": note_id,
                        "helpfulness": helpfulness,
                        "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    }
                )
            self.action(bot, "rate", helpfulness, f"note={note_id}")

    def _notes_of(self, behaviours: set[str], but: str) -> set[str]:
        """The ids of the notes written by personas with these behaviours, other than `but`."""
        with self.state.lock:
            return {
                note["id"]
                for name, record in self.state.data["personas"].items()
                if record.get("behaviour") in behaviours and name != but
                for note in record["notes"]
            }

    def _brigade_ratings(self, bot: Bot, notes: list[dict], n: int) -> list[tuple[str, str]]:
        """Members' notes helpful first, then everyone else's not helpful: the group's coordination."""
        members = self._notes_of({"brigade"}, bot.persona.name)
        ours = [note for note in notes if note["id"] in members]
        theirs = [note for note in notes if note["id"] not in members]
        random.shuffle(theirs)
        picked = (ours + theirs)[:n]
        return [(note["id"], "helpful" if note["id"] in members else "not_helpful") for note in picked]

    def _troll_ratings(self, bot: Bot, notes: list[dict], n: int) -> list[tuple[str, str]]:
        """Bad actors' notes helpful, honest notes on accurate outlets not helpful; the rest it leaves."""
        bad = self._notes_of(BAD, bot.persona.name)
        accurate = [site for site in self.config.sites.values() if site.accurate]
        ratings = [(note["id"], "helpful") for note in notes if note["id"] in bad]
        ratings += [
            (note["id"], "not_helpful")
            for note in notes
            if note["id"] not in bad and any(site.serves(note["url"]) for site in accurate)
        ]
        return random.sample(ratings, min(n, len(ratings)))
