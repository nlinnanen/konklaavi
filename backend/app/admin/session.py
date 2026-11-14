"""Admin sign-in: an account's email and password open a server-side session named by a cookie.

The cookie holds a random token and `admin_session` its SHA-256, so a database dump opens no session.
It is HTTP-only and SameSite=Lax, which keeps other sites from posting moderation actions with it.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException, Request
from psycopg import Connection

from app import trust
from app.db import connection

COOKIE = "hashtrust_admin"
LIFETIME = timedelta(days=7)


@dataclass(frozen=True)
class Admin:
    account_id: UUID
    email: str
    reputation: float
    moderator: bool


class SignInRequired(Exception):
    """Raised by a page that needs a session; the handler sends the browser to the sign-in form."""


def base_href(request: Request) -> str:
    """The admin root as the browser addresses it, ending in a slash.

    Behind a proxy that names its prefix (X-Forwarded-Prefix, or uvicorn's root_path) it is absolute.
    Otherwise it is relative to the requested page, which holds at /admin and under a prefix that the
    proxy strips without saying, as Caddy's handle_path /api/* does.
    """
    prefix = request.headers.get("x-forwarded-prefix") or request.scope.get("root_path") or ""
    if prefix:
        return prefix.rstrip("/") + "/admin/"
    rest = request.url.path.split("/admin", 1)[-1]
    depth = max(rest.count("/") - 1, 0)
    return "../" * depth or "./"


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def open_session(conn: Connection, account_id: UUID) -> str:
    """Store a new session for the account and return the token for its cookie."""
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO admin_session (token_hash, account_id, expires_at) VALUES (%s, %s, now() + %s)",
        (_hash(token), account_id, LIFETIME),
    )
    conn.execute("DELETE FROM admin_session WHERE expires_at < now()")
    return token


def close_session(conn: Connection, token: str) -> None:
    conn.execute("DELETE FROM admin_session WHERE token_hash = %s", (_hash(token),))


def signed_in(request: Request) -> Admin | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with connection() as conn:
        row = conn.execute(
            "SELECT a.id, a.email FROM admin_session s JOIN user_account a ON a.id = s.account_id"
            " WHERE s.token_hash = %s AND s.expires_at > now()",
            (_hash(token),),
        ).fetchone()
        if row is None:
            return None
        standing = trust.account_standing(conn, *row)
    return Admin(row[0], row[1], standing.reputation, standing.moderator)


def current_admin(request: Request) -> Admin:
    """Dependency of every admin page: the signed-in account."""
    admin = signed_in(request)
    if admin is None:
        raise SignInRequired
    return admin


def require_moderator(admin: Admin) -> None:
    if not admin.moderator:
        raise HTTPException(403, "Only moderators can do this.")


def secure(request: Request) -> bool:
    return (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"
