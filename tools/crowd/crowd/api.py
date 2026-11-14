"""The HashTrust public API, as docs/api.md defines it."""

import httpx


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class Api:
    def __init__(self, base: str):
        self.client = httpx.Client(
            base_url=base.rstrip("/"), timeout=60, headers={"User-Agent": "hashtrust-crowd"}
        )

    def _send(self, method: str, path: str, **kwargs) -> dict:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:200]
            raise ApiError(response.status_code, str(detail))
        return response.json()

    def me(self, user_id: str) -> dict:
        return self._send("GET", "/me", params={"userId": user_id})

    def sign_in(self, user_id: str, email: str, password: str) -> dict:
        """Register the account, or log in when it exists."""
        credentials = {"userId": user_id, "email": email, "password": password}
        try:
            return self._send("POST", "/auth/register", json=credentials)
        except ApiError as err:
            if err.status != 409:
                raise
            return self._send("POST", "/auth/login", json=credentials)

    def write_note(
        self, user_id: str, passage: str, url: str, classification: str, body: str, sources
    ) -> dict:
        return self._send(
            "POST",
            "/notes",
            json={
                "userId": user_id,
                "passage": {"text": passage, "url": url},
                "classification": classification,
                "body": body,
                "sources": sources,
            },
        )

    def recent_notes(self, limit: int = 100) -> list[dict]:
        return self._send("GET", "/notes/recent", params={"limit": limit})["notes"]

    def rate(self, user_id: str, note_id: str, helpfulness: str) -> None:
        self._send("POST", f"/notes/{note_id}/ratings", json={"userId": user_id, "helpfulness": helpfulness})
