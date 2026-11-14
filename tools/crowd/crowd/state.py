"""The local record of the bots on one API: their identities and every note and rating they made.

It is the ground truth for checking whether reputation separates good bots from bad ones, and holds the
generated passwords, so it stays out of git.
"""

import json
import os
import secrets
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from crowd.config import Persona

# Other names of a production host -> its canonical name, which names the state and log files.
ALIASES = {"srv2160986.hstgr.cloud": "konklaavi.tech"}


def host(api: str) -> str:
    name = urlsplit(api).netloc
    return ALIASES.get(name, name).replace(":", "_") or "api"


def paths_for(config_path: Path, api: str) -> tuple[Path, Path]:
    """The state file and action log of a run against the API, taking over those an alias named."""
    name = host(api)
    state = config_path.parent / f".crowd-state-{name}.json"
    log = config_path.parent / f"crowd-{name}.log"
    for alias in (a for a, canonical in ALIASES.items() if canonical == name):
        old_state = config_path.parent / f".crowd-state-{alias}.json"
        old_log = config_path.parent / f"crowd-{alias}.log"
        if old_state.exists() and not state.exists():
            old_state.rename(state)
            if old_log.exists() and not log.exists():
                old_log.rename(log)
    return state, log


class State:
    def __init__(self, path: Path, api: str):
        self.path = path
        # Held by whoever mutates or reads the records while workers run, and by save().
        self.lock = threading.RLock()
        self.data = json.loads(path.read_text()) if path.exists() else {"api": api, "personas": {}}

    def persona(self, persona: Persona, domain: str) -> dict:
        """The persona's record, created with a fresh install id (and account when it signs in)."""
        record = self.data["personas"].setdefault(
            persona.name, {"install": str(uuid.uuid4()), "notes": [], "ratings": [], "articles": []}
        )
        record["behaviour"] = persona.behaviour
        record["logged_in"] = persona.logged_in
        if persona.logged_in:
            record["email"] = f"bot-{persona.name}@{domain}"
            if not os.environ.get("CROWD_BOT_PASSWORD"):
                record.setdefault("password", secrets.token_urlsafe(18))
        return record

    def password(self, record: dict) -> str:
        return os.environ.get("CROWD_BOT_PASSWORD") or record["password"]

    def save(self) -> None:
        with self.lock:
            text = json.dumps(self.data, indent=1)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text)
            tmp.chmod(0o600)
            tmp.replace(self.path)
