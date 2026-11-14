import os
from dataclasses import dataclass


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


@dataclass(frozen=True)
class Settings:
    database_url: str
    fp_url: str
    anthropic_api_key: str | None
    anthropic_base_url: str | None
    # Small model that reviews submitted notes.
    moderation_model: str
    # Lowercased emails of the accounts that moderate notes.
    moderator_emails: frozenset[str]
    # Reputation above which an account moderates notes too.
    moderator_reputation: float
    # Counted ratings a note needs before scoring can decide its status.
    note_min_ratings: int
    # Counted ratings on other notes a rater needs before bridging can place them on the viewpoint axis.
    bridging_min_history: int
    # Mean helpfulness at or above which, or at or below which, agreement decides a note helpful or not.
    agreement_helpful: float
    agreement_not_helpful: float
    # Cosine at which a block matches a narrative's vectors.
    narrative_match_threshold: float
    # Cosine at which a block is related to a narrative, below the match threshold.
    related_match_threshold: float
    # Lowercased hostnames Konklaavi serves from, its demo proxy included; never tracked as pages.
    own_hosts: frozenset[str]
    # Commit the image was built from; CI bakes it in, and the deploy waits until /health reports it.
    version: str


def load() -> Settings:
    db_port = _env("DB_PORT") or "5432"
    fp_port = _env("FP_PORT") or "8100"
    return Settings(
        database_url=_env("DATABASE_URL") or f"postgresql://postgres:postgres@localhost:{db_port}/postgres",
        fp_url=(_env("FP_URL") or f"http://localhost:{fp_port}").rstrip("/"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_base_url=_env("ANTHROPIC_BASE_URL"),
        moderation_model=_env("MODERATION_MODEL") or "claude-haiku-5.5",
        moderator_emails=frozenset(
            email.strip().lower() for email in (_env("MODERATOR_EMAILS") or "").split(",") if email.strip()
        ),
        moderator_reputation=float(_env("MODERATOR_REPUTATION") or "0.8"),
        note_min_ratings=int(_env("NOTE_MIN_RATINGS") or "5"),
        bridging_min_history=int(_env("BRIDGING_MIN_HISTORY") or "3"),
        agreement_helpful=float(_env("AGREEMENT_HELPFUL") or "0.75"),
        agreement_not_helpful=float(_env("AGREEMENT_NOT_HELPFUL") or "0.25"),
        narrative_match_threshold=float(_env("NARRATIVE_MATCH_THRESHOLD") or "0.825"),
        related_match_threshold=float(_env("RELATED_MATCH_THRESHOLD") or "0.70"),
        own_hosts=frozenset(
            host.strip().lower() for host in (_env("OWN_HOSTS") or "").split(",") if host.strip()
        ),
        version=_env("APP_VERSION") or "dev",
    )


settings = load()
