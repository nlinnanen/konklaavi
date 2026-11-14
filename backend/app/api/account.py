"""Accounts and a user's standing.

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
from app.api.narratives import Body
from app.config import settings
from app.db import connection
from app.trust import standing

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


@router.get("/me")
def get_me(user_id: Annotated[UUID, Query(alias="userId")]) -> dict:
    """The account's email, reputation, notes and rating record; an unlinked install has none of them."""
    with connection() as conn:
        me = standing(conn, user_id)
        notes = conn.execute(
            """
            SELECT n.id, n.narrative_id, r.title, n.classification, n.body, n.status, n.moderation, n.created_at
            FROM note n JOIN narrative r ON r.id = n.narrative_id
            WHERE n.account_id = %s
            ORDER BY n.created_at DESC
            """,
            (me.account_id,),
        ).fetchall()
    return {
        "userId": str(user_id),
        "email": me.email,
        "verified": me.account_id is not None,
        "reputation": me.reputation,
        "moderator": me.moderator,
        "notes": [
            {
                "id": str(id_),
                "narrativeId": str(narrative_id),
                "title": title,
                "classification": classification,
                "body": body,
                "status": status,
                "moderation": moderation,
                "createdAt": created.isoformat(),
            }
            for id_, narrative_id, title, classification, body, status, moderation, created in notes
        ],
        "ratings": {"count": me.ratings, "agreed": me.agreed},
    }
