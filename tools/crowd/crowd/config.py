"""crowd.yaml: the sites the bots read and the personas that read them."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from crowd.behaviours import BEHAVIOURS


@dataclass(frozen=True)
class Persona:
    name: str
    behaviour: str
    logged_in: bool
    sites: list[str]
    writes_per_hour: float
    rates_per_hour: float


@dataclass(frozen=True)
class Site:
    feeds: list[str]
    # Reports accurately; the bad personas label its articles misleading and those of other sites not.
    accurate: bool
    # Domains its articles are served from; by default those of its feeds.
    domains: list[str]

    def serves(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        return any(host == d or host.endswith("." + d) for d in self.domains)


@dataclass(frozen=True)
class Config:
    domain: str
    sites: dict[str, Site]
    personas: list[Persona]


def load(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())
    sites = {
        name: Site(
            feeds=list(site["feeds"]),
            accurate=bool(site.get("accurate", False)),
            domains=list(site.get("domains") or {_domain(feed) for feed in site["feeds"]}),
        )
        for name, site in raw["sites"].items()
    }
    personas = []
    for p in raw["personas"]:
        persona = Persona(
            name=p["name"],
            behaviour=p["behaviour"],
            logged_in=bool(p.get("logged_in", False)),
            sites=list(p["sites"]),
            writes_per_hour=float(p.get("writes_per_hour", 2)),
            rates_per_hour=float(p.get("rates_per_hour", 10)),
        )
        if persona.behaviour not in BEHAVIOURS:
            raise SystemExit(f"{persona.name}: unknown behaviour {persona.behaviour}")
        if unknown := set(persona.sites) - set(sites):
            raise SystemExit(f"{persona.name}: unknown sites {sorted(unknown)}")
        personas.append(persona)
    if len({p.name for p in personas}) != len(personas):
        raise SystemExit("persona names must be unique")
    return Config(domain=raw.get("domain", "crowd.hashtrust.test"), sites=sites, personas=personas)


def _domain(url: str) -> str:
    """The feed's host without a feeds., rss. or www. label: feeds.yle.fi -> yle.fi."""
    host = urlsplit(url).hostname or ""
    for prefix in ("feeds.", "rss.", "www."):
        host = host.removeprefix(prefix)
    return host
