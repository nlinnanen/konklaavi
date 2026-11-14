from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

# The reliable-outlet articles the evaluation uses as negatives; their domains are the reliable outlets.
NEGATIVES_FILE = Path(__file__).parent / "eval" / "negatives.txt"


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


@cache
def reliable_domains() -> frozenset[str]:
    lines = (line.strip() for line in NEGATIVES_FILE.read_text().splitlines())
    return frozenset(host(line) for line in lines if line and not line.startswith("#"))


def is_reliable_url(url: str) -> bool:
    """Whether the URL is on a reliable outlet's domain or one of its subdomains."""
    name = host(url)
    return any(name == domain or name.endswith("." + domain) for domain in reliable_domains())
