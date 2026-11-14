"""Remove crowd-bot data: python -m app.demo.remove_bots --domain crowd.hashtrust.test [--install UUID ...].

The bots of tools/crowd sign in under one email domain or write as anonymous installs whose ids the runner
keeps. This deletes those accounts and installs, every note and rating they made, and each narrative their
notes created that holds no other note, then rescores.
"""

import argparse
import uuid

import psycopg
from psycopg import Connection

from app.config import settings
from app.demo.narratives import delete_narratives
from app.jobs.score import score


def remove(conn: Connection, domain: str, anonymous: list[uuid.UUID]) -> dict:
    """Delete the bots' accounts, installs, notes and ratings and their emptied narratives; returns counts."""
    accounts = [
        row[0] for row in conn.execute("SELECT id FROM user_account WHERE email LIKE %s", (f"%@{domain}",))
    ]
    installs = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM install WHERE account_id = ANY(%s) OR id = ANY(%s)", (accounts, anonymous)
        )
    ]
    notes, narratives = [], set()
    for note_id, narrative_id in conn.execute(
        "SELECT id, narrative_id FROM note WHERE author_install = ANY(%s) OR account_id = ANY(%s)",
        (installs, accounts),
    ):
        notes.append(note_id)
        narratives.add(narrative_id)
    ratings = conn.execute(
        "DELETE FROM note_rating WHERE note_id = ANY(%s) OR install = ANY(%s) OR account_id = ANY(%s)",
        (notes, installs, accounts),
    ).rowcount
    conn.execute("DELETE FROM note WHERE id = ANY(%s)", (notes,))
    emptied = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM narrative r WHERE id = ANY(%s) AND NOT EXISTS (SELECT 1 FROM note WHERE narrative_id = r.id)",
            (list(narratives),),
        )
    ]
    delete_narratives(conn, emptied)
    conn.execute("DELETE FROM auth_attempt WHERE email LIKE %s", (f"%@{domain}",))
    conn.execute("DELETE FROM admin_session WHERE account_id = ANY(%s)", (accounts,))
    conn.execute("DELETE FROM install WHERE id = ANY(%s)", (installs,))
    conn.execute("DELETE FROM user_account WHERE id = ANY(%s)", (accounts,))
    score(conn)
    return {
        "accounts": len(accounts),
        "installs": len(installs),
        "notes": len(notes),
        "ratings": ratings,
        "narratives": len(emptied),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", required=True, help="email domain of the bot accounts")
    parser.add_argument(
        "--install", type=uuid.UUID, action="append", default=[], help="anonymous bot install id"
    )
    args = parser.parse_args()
    if "." not in args.domain or args.domain.startswith("@"):
        parser.error("--domain takes a bare domain such as crowd.hashtrust.test")
    with psycopg.connect(settings.database_url) as conn:
        counts = remove(conn, args.domain, args.install)
    print("removed " + ", ".join(f"{n} {what}" for what, n in counts.items()))


if __name__ == "__main__":
    main()
