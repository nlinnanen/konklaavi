import itertools
import threading
import time

from crowd import web
from crowd.web import Robots, clean

RT = """
User-agent: Googlebot
Disallow: /

User-agent: *
Disallow: /search*
Disallow: /*/video/
Disallow: *?
Allow: /static/js/
"""


def test_robots_rules_match_with_wildcards_for_the_star_group():
    robots = Robots(RT)
    assert robots.allows("https://rt-english.com/news/648838-poland/")
    assert not robots.allows("https://rt-english.com/news/648838-poland/?utm_source=rss")
    assert not robots.allows("https://rt-english.com/russia/video/123/")
    assert not robots.allows("https://rt-english.com/search?q=x")


def test_the_longest_rule_wins_and_allow_wins_a_tie():
    robots = Robots("User-agent: *\nDisallow: /news/\nAllow: /news/public\nDisallow: /a\nAllow: /a")
    assert robots.allows("https://x.test/news/public/1")
    assert not robots.allows("https://x.test/news/other")
    assert robots.allows("https://x.test/a")


def test_a_group_naming_the_crowd_agent_replaces_the_star_group():
    robots = Robots("User-agent: *\nDisallow: /\n\nUser-agent: konklaavi-crowd\nDisallow: /private")
    assert robots.allows("https://x.test/news")
    assert not robots.allows("https://x.test/private/1")


def test_feed_tracking_parameters_are_dropped():
    url = "https://www.bbc.co.uk/news/articles/c1?at_medium=RSS&at_campaign=rss&page=2"
    assert clean(url) == "https://www.bbc.co.uk/news/articles/c1?page=2"


def test_requests_to_one_host_keep_the_interval_across_threads(monkeypatch):
    monkeypatch.setattr(web, "MIN_INTERVAL", 0.2)
    reader = web.Web()
    starts: list[float] = []
    monkeypatch.setattr(reader.client, "get", lambda url: starts.append(time.monotonic()))
    threads = [threading.Thread(target=reader._get, args=("https://x.test/a",)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert len(starts) == 4 and min(gaps) >= 0.2
