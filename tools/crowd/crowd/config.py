"""crowd.yaml: the sites the bots read and the personas that read them."""

from dataclasses import dataclass
from pathlib import Path

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
class Config:
    domain: str
    sites: dict[str, list[str]]
    personas: list[Persona]


def load(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())
    sites = {name: list(site["feeds"]) for name, site in raw["sites"].items()}
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
