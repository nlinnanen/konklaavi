"""Accounts and a user's standing.

An install signs in to an account with its email and password, which links the install to the account
and gives the account the notes the install wrote without one.
The email is not verified: it names the account and decides moderators through MODERATOR_EMAILS.
"""

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, HTTPException, Query
from psycopg import Connection
from pydantic import Field

from app.api.narratives import Body
from app.db import connection
from app.trust import standing

router = APIRouter()
hasher = PasswordHasher()

# Failed sign-in and registration attempts allowed per email, and per install, within RATE_WINDOW.
# A successful sign-in clears them, so only guessing is limited.
RATE_LIMIT = 10
RATE_WINDOW = timedelta(hours=1)


class Credentials(Body):
    user_id: UUID
    email: str = Field(max_length=254, pattern=r"^\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*$")
    password: str = Field(min_length=8, max_length=1024)


class Install(Body):
    user_id: UUID


def attempt(conn: Connection, email: str, install_id: UUID | None) -> str:
    """Record a sign-in attempt and return the lowercased email; 429 past the rate limit.

    Without an install, as for the admin panel, only the email's attempts count.
    """
    email = email.strip().lower()
    per_email, per_install = conn.execute(
        "SELECT count(*) FILTER (WHERE email = %(email)s), count(*) FILTER (WHERE install_id = %(install)s)"
        " FROM auth_attempt WHERE (email = %(email)s OR install_id = %(install)s)"
        " AND created_at > now() - %(window)s",
        {"email": email, "install": install_id, "window": RATE_WINDOW},
    ).fetchone()
    if max(per_email, per_install) >= RATE_LIMIT:
        raise HTTPException(429, "too many sign-in attempts, try again later")
    conn.execute("INSERT INTO auth_attempt (email, install_id) VALUES (%s, %s)", (email, install_id))
    # The attempt counts even when the request fails.
    conn.commit()
    return email


def link(conn: Connection, install_id: UUID, account_id: UUID, email: str) -> None:
    conn.execute("DELETE FROM auth_attempt WHERE email = %s OR install_id = %s", (email, install_id))
    conn.execute(
        "INSERT INTO install (id, account_id) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET account_id = %s",
        (install_id, account_id, account_id),
    )
    conn.execute(
        "UPDATE note SET account_id = %s WHERE author_install = %s AND account_id IS NULL",
        (account_id, install_id),
    )


def check_password(conn: Connection, email: str, password: str) -> UUID:
    """The account of the lowercased email when the password is its own; 401 otherwise."""
    row = conn.execute("SELECT id, password_hash FROM user_account WHERE email = %s", (email,)).fetchone()
    try:
        if row is None or row[1] is None:
            raise VerificationError
        hasher.verify(row[1], password)
    except VerificationError as error:
        raise HTTPException(401, "wrong email or password") from error
    account_id, password_hash = row
    if hasher.check_needs_rehash(password_hash):
        conn.execute(
            "UPDATE user_account SET password_hash = %s WHERE id = %s", (hasher.hash(password), account_id)
        )
    return account_id


@router.post("/auth/register")
def post_register(request: Credentials) -> dict:
    """Create the account for the email and sign the install in; 409 when the email has a password.

    An account without a password, made before sign-in had passwords, takes the new one.
    """
    with connection() as conn:
        email = attempt(conn, request.email, request.user_id)
        row = conn.execute(
            "INSERT INTO user_account (email, password_hash) VALUES (%s, %s)"
            " ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash"
            " WHERE user_account.password_hash IS NULL RETURNING id",
            (email, hasher.hash(request.password)),
        ).fetchone()
        if row is None:
            raise HTTPException(409, "an account with this email exists, sign in instead")
        link(conn, request.user_id, row[0], email)
    return get_me(request.user_id)


@router.post("/auth/login")
def post_login(request: Credentials) -> dict:
    """Sign the install in to the account; 401 for an unknown email or a wrong password."""
    with connection() as conn:
        email = attempt(conn, request.email, request.user_id)
        link(conn, request.user_id, check_password(conn, email, request.password), email)
    return get_me(request.user_id)


@router.post("/auth/logout")
def post_logout(request: Install) -> dict:
    """Unlink the install from its account."""
    with connection() as conn:
        conn.execute("UPDATE install SET account_id = NULL WHERE id = %s", (request.user_id,))
    return get_me(request.user_id)


@router.get("/me")
def get_me(user_id: Annotated[UUID, Query(alias="userId")]) -> dict:
    """The account's email, reputation, notes and rating record.

    An install without an account has no email, the capped reputation of a new account and the notes it wrote.
    """
    with connection() as conn:
        me = standing(conn, user_id)
        notes = conn.execute(
            """
            SELECT n.id, n.narrative_id, r.title, n.classification, n.body, n.status, n.moderation, n.created_at
            FROM note n JOIN narrative r ON r.id = n.narrative_id
            WHERE n.account_id = %s OR (n.account_id IS NULL AND n.author_install = %s)
            ORDER BY n.created_at DESC
            """,
            (me.account_id, user_id),
        ).fetchall()
    return {
        "userId": str(user_id),
        "email": me.email,
        "verified": me.account_id is not None,
        "reputation": me.reputation,
        "reputationCapped": me.capped,
        "reputationHint": "Sign in to keep your reputation and let it grow." if me.capped else None,
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
