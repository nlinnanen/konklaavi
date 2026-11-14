"""Polite reading of news sites: robots.txt, at least MIN_INTERVAL seconds between requests to a host,
feeds through feedparser and article text through trafilatura."""

import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
import trafilatura

USER_AGENT = "konklaavi-crowd"
MIN_INTERVAL = 3.0
# Text shorter than this is a paywall, a consent wall or a video page.
MIN_ARTICLE_CHARS = 800
FEED_TTL = timedelta(minutes=15)
MAX_AGE = timedelta(days=3)

log = logging.getLogger(__name__)


class Robots:
    """One host's robots.txt rules for USER_AGENT, matched as Google matches them: `*` and `$` wildcards,
    the longest matching rule wins and Allow wins a tie."""

    def __init__(self, text: str, agent: str = USER_AGENT):
        groups: list[tuple[set[str], list[tuple[bool, str]]]] = []
        agents: set[str] = set()
        rules: list[tuple[bool, str]] = []
        for raw in text.splitlines():
            key, _, value = raw.split("#", 1)[0].partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if rules:
                    groups.append((agents, rules))
                    agents, rules = set(), []
                agents.add(value.lower())
            elif key in ("allow", "disallow") and agents and value:
                rules.append((key == "allow", value))
        if agents:
            groups.append((agents, rules))
        mine = [r for a, r in groups if agent.lower() in a]
        self.rules = [rule for r in (mine or [r for a, r in groups if "*" in a]) for rule in r]

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] = (-1, True)
        for allow, pattern in self.rules:
            regex = "".join(".*" if c == "*" else "$" if c == "$" else re.escape(c) for c in pattern)
            if re.match(regex, path) and (len(pattern), allow) > best:
                best = (len(pattern), allow)
        return best[1]


def clean(url: str) -> str:
    """The URL without the tracking parameters feeds add (utm_*, BBC's at_*, Yle's origin)."""
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.startswith(("utm_", "at_")) and k != "origin"
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


@dataclass
class Article:
    url: str
    title: str
    text: str


class Web:
    def __init__(self):
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True, max_redirects=5
        )
        self.last_request: dict[str, float] = {}
        self.host_locks: dict[str, threading.Lock] = {}
        self.locks_lock = threading.Lock()
        self.robots: dict[str, Robots] = {}
        self.feeds: dict[str, tuple[datetime, list[tuple[str, str]]]] = {}

    def get(self, url: str) -> httpx.Response | None:
        """GET the URL when robots.txt allows it, waiting out the host's interval; None when not allowed."""
        if not self._robots(url).allows(url):
            log.info("robots.txt disallows %s", url)
            return None
        response = self._get(url)
        # A redirect to another host is checked against that host's rules too.
        if urlsplit(str(response.url)).netloc != urlsplit(url).netloc and not self._robots(
            str(response.url)
        ).allows(str(response.url)):
            return None
        return response

    def _get(self, url: str) -> httpx.Response:
        """One request at a time per host across all workers, MIN_INTERVAL after the previous one ended."""
        host = urlsplit(url).netloc
        with self.locks_lock:
            lock = self.host_locks.setdefault(host, threading.Lock())
        with lock:
            wait = self.last_request.get(host, 0) + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                return self.client.get(url)
            finally:
                self.last_request[host] = time.monotonic()

    def _robots(self, url: str) -> Robots:
        parts = urlsplit(url)
        if parts.netloc not in self.robots:
            try:
                response = self._get(f"{parts.scheme}://{parts.netloc}/robots.txt")
                if response.status_code in (401, 403):
                    rules = Robots("User-agent: *\nDisallow: /")
                elif response.is_success and "html" not in response.headers.get("content-type", ""):
                    rules = Robots(response.text)
                else:
                    rules = Robots("")
            except httpx.HTTPError as err:
                # Unreachable for now: disallow this request and ask again next time.
                log.warning("robots.txt of %s: %s", parts.netloc, err)
                return Robots("User-agent: *\nDisallow: /")
            self.robots[parts.netloc] = rules
        return self.robots[parts.netloc]

    def entries(self, feed: str) -> list[tuple[str, str]]:
        """(url, title) of the feed's articles from the last MAX_AGE, refreshed every FEED_TTL."""
        now = datetime.now(UTC)
        cached = self.feeds.get(feed)
        if cached and now - cached[0] < FEED_TTL:
            return cached[1]
        entries: list[tuple[str, str]] = []
        try:
            response = self.get(feed)
            if response is not None and response.is_success:
                for entry in feedparser.parse(response.content).entries:
                    published = entry.get("published_parsed") or entry.get("updated_parsed")
                    if published and now - datetime(*published[:6], tzinfo=UTC) > MAX_AGE:
                        continue
                    if entry.get("link"):
                        entries.append((clean(entry.link), entry.get("title", "")))
            elif response is not None:
                log.warning("feed %s: HTTP %s", feed, response.status_code)
        except httpx.HTTPError as err:
            log.warning("feed %s: %s", feed, err)
        self.feeds[feed] = (now, entries)
        return entries

    def article(self, feeds: list[str], skip: set[str]) -> Article | None:
        """A random recent article from the feeds not in skip, with its extracted text; None when none reads."""
        candidates = [e for feed in feeds for e in self.entries(feed) if e[0] not in skip]
        random.shuffle(candidates)
        for url, title in candidates[:3]:
            skip.add(url)
            try:
                response = self.get(url)
            except httpx.HTTPError as err:
                log.info("article %s: %s", url, err)
                continue
            if response is None or not response.is_success:
                continue
            text = trafilatura.extract(response.text, include_comments=False, include_tables=False) or ""
            if len(text) >= MIN_ARTICLE_CHARS:
                return Article(str(response.url), title, text)
        return None
