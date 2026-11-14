"""Reputation of note writers and raters, and who moderates.

Reputation belongs to a verified account, counting the notes and ratings of all its installs; an install
without an account has none. It is (good + 1) / (good + bad + 4), so a new account starts at 0.25 and
track record moves it towards 1 or 0:

- A note of the account that scoring rated helpful adds 2 to good, one rated not helpful adds 2 to bad.
- A rating cast before its note's status was decided agrees when it matches the decision (helpful or
  somewhat helpful on a helpful note, not helpful on a not-helpful note) and adds 1 to good; otherwise it
  adds 1 to bad. Ratings cast after the decision count for neither, so following the crowd earns nothing,
  and counting disagreements keeps rating everything from paying.

Moderators are the accounts whose email is in MODERATOR_EMAILS and those whose reputation is above
MODERATOR_REPUTATION.
"""

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from app.config import settings

NOTE_WEIGHT = 2


@dataclass(frozen=True)
class Standing:
    account_id: UUID | None
    email: str | None
    helpful_notes: int = 0
    unhelpful_notes: int = 0
    ratings: int = 0
    agreed: int = 0
    disagreed: int = 0

    @property
    def reputation(self) -> float | None:
        if self.account_id is None:
            return None
        good = NOTE_WEIGHT * self.helpful_notes + self.agreed
        bad = NOTE_WEIGHT * self.unhelpful_notes + self.disagreed
        return (good + 1) / (good + bad + 4)

    @property
    def moderator(self) -> bool:
        if self.account_id is None:
            return False
        return self.email in settings.moderator_emails or self.reputation > settings.moderator_reputation


def standing(conn: Connection, user_id: UUID) -> Standing:
    """The standing of the account the install is linked to."""
    account = conn.execute(
        "SELECT a.id, a.email FROM install i JOIN user_account a ON a.id = i.account_id WHERE i.id = %s",
        (user_id,),
    ).fetchone()
    if account is None:
        return Standing(None, None)
    return account_standing(conn, *account)


def account_standing(conn: Connection, account_id: UUID, email: str) -> Standing:
    """The standing of an account, counting the notes and ratings of all its installs."""
    helpful, unhelpful = conn.execute(
        "SELECT count(*) FILTER (WHERE status = 'helpful'), count(*) FILTER (WHERE status = 'not_helpful')"
        " FROM note WHERE account_id = %s",
        (account_id,),
    ).fetchone()
    ratings, agreed, disagreed = conn.execute(
        """
        WITH latest AS (
          SELECT DISTINCT ON (r.note_id) r.note_id, r.helpfulness, r.created_at
          FROM note_rating r JOIN install i ON i.id = r.install
          WHERE i.account_id = %s
          ORDER BY r.note_id, r.created_at DESC
        ), early AS (
          SELECT l.*, n.status FROM latest l JOIN note n ON n.id = l.note_id
          WHERE n.decided_at IS NOT NULL AND l.created_at < n.decided_at
        )
        SELECT (SELECT count(*) FROM latest),
               count(*) FILTER (WHERE (status = 'helpful') = (helpfulness <> 'not_helpful')),
               count(*) FILTER (WHERE (status = 'helpful') <> (helpfulness <> 'not_helpful'))
        FROM early
        """,
        (account_id,),
    ).fetchone()
    return Standing(account_id, email, helpful, unhelpful, ratings, agreed, disagreed)
