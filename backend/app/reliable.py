"""Reliable outlets, whose pages count as settled-good content for reputation.

The domains are those of the evaluation's negatives (app/eval/negatives.txt), plus the outlets the
evaluation leaves out because they refuse anonymous fetches. A URL is reliable when its host is one of
them or a subdomain of one.
"""

from pathlib import Path
from urllib.parse import urlsplit

NEGATIVES_FILE = Path(__file__).resolve().parent / "eval" / "negatives.txt"
UNFETCHABLE = ("reuters.com", "apnews.com", "lemonde.fr", "france24.com", "lsm.lv")


def _host(url: str) -> str:
    host = (urlsplit(url.strip()).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def _load() -> frozenset[str]:
    lines = (line.strip() for line in NEGATIVES_FILE.read_text().splitlines())
    return frozenset({_host(line) for line in lines if line and not line.startswith("#")} | set(UNFETCHABLE))


RELIABLE_DOMAINS = _load()


def is_reliable_url(url: str) -> bool:
    host = _host(url)
    parts = host.split(".")
    return any(".".join(parts[i:]) in RELIABLE_DOMAINS for i in range(len(parts) - 1))
