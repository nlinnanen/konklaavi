"""Seed the Pravda network (Portal Kombat): recent articles of a sample of its sites become fetch jobs
for one source-level narrative.

The sites come from VIGINUM's published domain list. Each site's robots.txt is honoured, its news
sitemap lists the recent articles, and each site gets at most one request per second.

python -m app.seed.pravda [--sites N] [--limit N] [--days N]
"""

import argparse
import csv
import io
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from psycopg import Connection

from app.db import connection
from app.jobs import enqueue

# VIGINUM's list of Portal Kombat domains, pinned to the commit of its 10 April 2025 update.
DOMAINS_CSV = (
    "https://raw.githubusercontent.com/VIGINUM-FR/Rapports-Techniques/"
    "d516141d863fcc7898a11d86ddad4f5169566649/202402%20-%20Portal%20Kombat/"
    "20241227_SGDSN_VIGINUM_NP_TLP-CLEAR_Portal-Kombat-domains.csv"
)
REPORT_URL = (
    "https://www.sgdsn.gouv.fr/files/files/20240212_NP_SGDSN_VIGINUM_PORTAL-KOMBAT-NETWORK_ENG_VF.pdf"
)
TITLE = "Pravda network (Portal Kombat)"
CLAIM = (
    "Articles published by the Pravda network, which republishes pro-Russian content from Russian state"
    " media, official accounts and Telegram channels across hundreds of sites."
)
EXPLANATION = (
    "France's VIGINUM documented these sites in February 2024 as Portal Kombat, a structured and"
    " coordinated pro-Russian propaganda network. The label is for the network as a source: an article"
    " copied from it shares the network's origin, which says nothing on its own about whether each"
    " claim in it is true."
)
HUB = "news-pravda.com"
USER_AGENT = "hashtrust"
TIMEOUT = 30.0
PER_SITE_INTERVAL = 1.0
NEWS_NS = {
    "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}
# Serialises concurrent seeds, which would otherwise create the narrative twice.
_LOCK_KEY = 0x70726176

log = logging.getLogger(__name__)


def parse_domains(text: str) -> list[str]:
    """The domains of VIGINUM's CSV in file order, refanged from `a[.]b` to `a.b`."""
    rows = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    return [row["domain"].replace("[.]", ".").strip().lower() for row in rows if row["domain"].strip()]


def hub_sites(domains: list[str]) -> list[str]:
    """The listed subdomains of the network's hub, where its current sites live."""
    return [d for d in domains if d.endswith("." + HUB)]


@dataclass(frozen=True)
class Entry:
    url: str
    published: datetime


def parse_news_sitemap(xml: bytes, since: datetime) -> list[Entry]:
    """The articles of a Google News sitemap published at or after `since`, newest first."""
    entries = []
    for url in ET.fromstring(xml).iterfind("s:url", NEWS_NS):
        loc = url.findtext("s:loc", namespaces=NEWS_NS)
        stamp = url.findtext("news:news/news:publication_date", namespaces=NEWS_NS) or url.findtext(
            "s:lastmod", namespaces=NEWS_NS
        )
        if not loc or not stamp:
            continue
        try:
            published = datetime.fromisoformat(stamp.strip())
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        if published >= since:
            entries.append(Entry(loc.strip(), published))
    return sorted(entries, key=lambda e: e.published, reverse=True)


class Site:
    """One site of the network, requested at most once per PER_SITE_INTERVAL."""

    def __init__(self, client: httpx.Client, host: str):
        self.client = client
        self.host = host
        self.robots = RobotFileParser()
        self._last = 0.0

    def get(self, url: str) -> httpx.Response:
        wait = self._last + PER_SITE_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return self.client.get(url)
        finally:
            self._last = time.monotonic()

    def open(self) -> list[str]:
        """Read robots.txt, following a redirect to the site's current host; return its news sitemaps."""
        response = self.get(f"https://{self.host}/robots.txt")
        self.host = response.url.host
        if response.status_code in (401, 403):
            self.robots.disallow_all = True
        elif response.status_code >= 400:
            self.robots.allow_all = True
        else:
            self.robots.parse(response.text.splitlines())
        own = [
            url
            for url in self.robots.site_maps() or []
            if urlsplit(url).hostname == self.host and url.endswith("sitemap_news.xml")
        ]
        return own or [f"https://{self.host}/sitemap_news.xml"]

    def allowed(self, url: str) -> bool:
        return self.robots.can_fetch(USER_AGENT, url)


def recent_articles(sites: list[str], limit: int, since: datetime) -> list[str]:
    """Up to `limit` article URLs published since `since`, spread evenly over the sites."""
    per_site = -(-limit // max(len(sites), 1))
    urls: list[str] = []
    seen_hosts = set()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=TIMEOUT) as client:
        for host in sites:
            if len(urls) >= limit:
                break
            site = Site(client, host)
            try:
                sitemaps = site.open()
                if site.host in seen_hosts:
                    continue
                seen_hosts.add(site.host)
                found: list[str] = []
                for sitemap in sitemaps:
                    if not site.allowed(sitemap):
                        continue
                    response = site.get(sitemap)
                    if response.status_code != 200:
                        log.warning("%s: HTTP %d", sitemap, response.status_code)
                        continue
                    found += [
                        e.url for e in parse_news_sitemap(response.content, since) if site.allowed(e.url)
                    ]
            except (httpx.HTTPError, ET.ParseError) as error:
                log.warning("%s: %s", host, error)
                continue
            picked = list(dict.fromkeys(found))[: min(per_site, limit - len(urls))]
            log.info("%s: %d recent articles, %d taken", site.host, len(found), len(picked))
            urls += picked
    return urls


def network_narrative(conn: Connection) -> int:
    """The id of the Pravda network narrative, created when missing."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
    row = conn.execute(
        "SELECT id FROM narrative WHERE source = 'network' AND source_url = %s", (REPORT_URL,)
    ).fetchone()
    if row is not None:
        return row[0]
    return conn.execute(
        "INSERT INTO narrative (title, claim, explanation, verdict, source, source_url, state)"
        " VALUES (%s, %s, %s, 'manipulative', 'network', %s, 'active') RETURNING id",
        (TITLE, CLAIM, EXPLANATION, REPORT_URL),
    ).fetchone()[0]


def enqueue_new(conn: Connection, narrative_id: int, urls: list[str]) -> int:
    """Enqueue a seed fetch for each URL with no page and no fetch job yet; return how many."""
    known = {
        row[0]
        for row in conn.execute(
            "SELECT url FROM page WHERE url = ANY(%(urls)s)"
            " UNION SELECT payload->>'url' FROM job WHERE kind = 'fetch' AND payload->>'url' = ANY(%(urls)s)",
            {"urls": urls},
        )
    }
    new = [url for url in urls if url not in known]
    for url in new:
        enqueue(conn, "fetch", {"url": url, "reason": "seed", "narrative_id": narrative_id})
    return len(new)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sites", type=int, default=15, help="sample the first N listed hub sites")
    parser.add_argument("--limit", type=int, default=300, help="at most N article URLs in all")
    parser.add_argument("--days", type=int, default=7, help="articles published in the last N days")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    response = httpx.get(DOMAINS_CSV, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    response.raise_for_status()
    domains = parse_domains(response.text)
    sites = hub_sites(domains)[: args.sites]
    since = datetime.now(UTC) - timedelta(days=args.days)
    urls = recent_articles(sites, args.limit, since)
    with connection() as conn:
        narrative_id = network_narrative(conn)
        jobs = enqueue_new(conn, narrative_id, urls)
    print(
        f"{len(domains)} listed domains, {len(sites)} sites sampled: {len(urls)} recent articles,"
        f" {jobs} new fetch jobs for narrative {narrative_id}"
    )


if __name__ == "__main__":
    main()
