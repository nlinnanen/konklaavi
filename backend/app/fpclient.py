import httpx

from app.config import settings


def fingerprint(blocks: list[str], embed: bool = False) -> dict:
    """POST /fingerprint on the fingerprint service; the response shape is in docs/api.md."""
    response = httpx.post(
        f"{settings.fp_url}/fingerprint", json={"blocks": blocks, "embed": embed}, timeout=120
    )
    response.raise_for_status()
    return response.json()
