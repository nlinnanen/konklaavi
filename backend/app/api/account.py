"""Optional accounts and a voter's standing.

A magic link sent by email links the extension's install to an account for that email. Tokens are
single-use, stored as hashes, and expire after TOKEN_LIFETIME.
"""

import hashlib
import html
import logging
import secrets
import smtplib
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import Field

from app import mail
from app.api.core import Body
from app.config import settings
from app.db import connection
from app.trust import standings

router = APIRouter()
log = logging.getLogger(__name__)

TOKEN_LIFETIME = timedelta(minutes=30)
# Links sent per email, and per install, within RATE_WINDOW.
RATE_LIMIT = 5
RATE_WINDOW = timedelta(hours=1)


class MagicLinkRequest(Body):
    user_id: UUID
    email: str = Field(max_length=254, pattern=r"^\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*$")


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@router.post("/auth/magic-link")
def post_magic_link(request: MagicLinkRequest) -> dict:
    """Email a sign-in link, or log it when SMTP_URL is unset."""
    email = request.email.strip().lower()
    token = secrets.token_urlsafe(32)
    with connection() as conn:
        conn.execute("INSERT INTO install (id) VALUES (%s) ON CONFLICT DO NOTHING", (request.user_id,))
        per_email, per_install = conn.execute(
            "SELECT count(*) FILTER (WHERE email = %(email)s), count(*) FILTER (WHERE install_id = %(install)s)"
            " FROM login_token WHERE (email = %(email)s OR install_id = %(install)s)"
            " AND created_at > now() - %(window)s",
            {"email": email, "install": request.user_id, "window": RATE_WINDOW},
        ).fetchone()
        if max(per_email, per_install) >= RATE_LIMIT:
            raise HTTPException(429, "too many sign-in links, try again later")
        conn.execute(
            "INSERT INTO login_token (token_hash, install_id, email, expires_at) VALUES (%s, %s, %s, now() + %s)",
            (token_hash(token), request.user_id, email, TOKEN_LIFETIME),
        )
    link = f"{settings.public_api_url}/auth/verify?{urlencode({'token': token})}"
    if not settings.smtp_url:
        log.warning("SMTP_URL is unset; sign-in link for %s: %s", email, link)
        return {"ok": True}
    body = f"Open this link to sign in to HashTrust:\n\n{link}\n\nThe link works once, within 30 minutes."
    try:
        mail.send(email, "Sign in to HashTrust", body)
    except (OSError, smtplib.SMTPException) as error:
        log.exception("sending the sign-in link failed")
        raise HTTPException(502, "could not send the sign-in email") from error
    return {"ok": True}


def page(message: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>HashTrust</title><p style='font:16px system-ui;margin:3em auto;max-width:30em'>{message}</p>",
        status_code=status_code,
    )


@router.get("/auth/verify", response_class=HTMLResponse)
def get_verify(token: str) -> HTMLResponse:
    """Consume the token and link its install to the account for its email, creating the account."""
    with connection() as conn:
        row = conn.execute(
            "UPDATE login_token SET used_at = now()"
            " WHERE token_hash = %s AND used_at IS NULL AND expires_at > now() RETURNING install_id, email",
            (token_hash(token),),
        ).fetchone()
        if row is None:
            return page("This sign-in link is invalid, used or expired. Request a new one.", 400)
        install_id, email = row
        account_id = conn.execute(
            "INSERT INTO user_account (email) VALUES (%s)"
            " ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id",
            (email,),
        ).fetchone()[0]
        conn.execute("UPDATE install SET account_id = %s WHERE id = %s", (account_id, install_id))
    return page(f"Signed in as {html.escape(email)}, you can close this tab.")


def outcome(state: str, settled_by: int | None) -> str:
    if state in ("retired", "rejected"):
        return "rejected"
    return "confirmed" if settled_by is not None else "pending"


@router.get("/me")
def get_me(user_id: Annotated[UUID, Query(alias="userId")]) -> dict:
    """The voter's email, reputation and the reports of all installs of its account."""
    with connection() as conn:
        standing = standings(conn, [user_id])[user_id]
        account = conn.execute(
            "SELECT a.email FROM install i JOIN user_account a ON a.id = i.account_id WHERE i.id = %s",
            (user_id,),
        ).fetchone()
        reports = conn.execute(
            """
            SELECT n.id, n.title, n.state, n.settled_by, s.source, s.title, s.source_url,
                   min(v.received_at), n.settled_at
            FROM vote v
            JOIN narrative n ON n.id = v.narrative_id AND n.source = 'report'
            LEFT JOIN narrative s ON s.id = n.settled_by
            WHERE v.verdict <> 'trustworthy'
              AND (v.user_id = %(user)s OR v.user_id IN (SELECT id FROM install WHERE account_id = %(identity)s))
            GROUP BY n.id, s.id
            ORDER BY min(v.received_at) DESC
            """,
            {"user": user_id, "identity": standing.identity},
        ).fetchall()
    return {
        "userId": str(user_id),
        "email": account[0] if account else None,
        "reputation": standing.reputation,
        "weight": standing.reputation,
        "agreed": standing.agreed,
        "disagreed": standing.disagreed,
        "reports": [
            {
                "narrativeId": str(narrative_id),
                "title": title,
                "state": state,
                "outcome": outcome(state, settled_by),
                "settledBy": (
                    {"source": source, "title": settled_title, "sourceUrl": source_url}
                    if settled_by is not None
                    else None
                ),
                "reportedAt": reported_at,
                "settledAt": settled_at,
            }
            for narrative_id, title, state, settled_by, source, settled_title, source_url, reported_at, settled_at in reports
        ],
    }
