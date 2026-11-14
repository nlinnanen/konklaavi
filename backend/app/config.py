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
    analysis_model: str | None
    # Cosine at which a block matches a narrative's vectors.
    narrative_match_threshold: float


def load() -> Settings:
    db_port = _env("DB_PORT") or "5432"
    fp_port = _env("FP_PORT") or "8100"
    return Settings(
        database_url=_env("DATABASE_URL") or f"postgresql://postgres:postgres@localhost:{db_port}/postgres",
        fp_url=(_env("FP_URL") or f"http://localhost:{fp_port}").rstrip("/"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_base_url=_env("ANTHROPIC_BASE_URL"),
        analysis_model=_env("ANALYSIS_MODEL"),
        narrative_match_threshold=float(_env("NARRATIVE_MATCH_THRESHOLD") or "0.825"),
    )


settings = load()
