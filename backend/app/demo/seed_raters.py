"""Seed demo rater history so bridging has viewpoints to learn: python -m app.demo.seed_raters [--remove].

Two camps of demo accounts (demo-camp-a-N@hashtrust.demo, demo-camp-b-N@hashtrust.demo) rate notes on
demo narratives whose passages are synthetic text no real page carries. The camps split on the divisive
notes and agree on the cross-camp ones and on an unhelpful one, which gives the factorisation an axis to place later raters on.
Each account's password is random and discarded, so nobody can sign in as one, and the .demo domain keeps
anyone from registering the email first.

Seeding is idempotent. --remove deletes the demo accounts, their installs, the demo narratives and every
note and rating on them. Both rescore the notes.
"""

import argparse
import secrets
import uuid

import psycopg
from argon2 import PasswordHasher
from psycopg import Connection

from app.config import settings
from app.demo.narratives import delete_narratives
from app.jobs.score import score

DOMAIN = "hashtrust.demo"
URL_PREFIX = f"https://{DOMAIN}/seed/"
CAMP_SIZE = 5
INSTALL_NAMESPACE = uuid.UUID("5d1c3a52-7f0e-4c43-9a51-6e0f2b8d4a17")

# (slug, passage, [(author, classification, body, raters who find it helpful: 'a', 'b', 'both' or 'none')])
NARRATIVES = [
    (
        "four-day-week",
        (
            "HashTrust demo passage: a four-day work week makes every company more productive. "
            "Offices that switch never go back."
        ),
        [
            ("a-1", "not_misleading", "Demo note: trials in several countries reported steady output.", "a"),
            (
                "b-1",
                "misleading",
                "Demo note: results vary by sector and many trials were self-selected.",
                "b",
            ),
            ("a-2", "not_misleading", "Demo note: the trials measured a 32-hour week at full pay.", "both"),
        ],
    ),
    (
        "daylight-saving",
        (
            "HashTrust demo passage: ending daylight saving time would save every household money. "
            "The clock change has no benefits left."
        ),
        [
            ("b-2", "misleading", "Demo note: energy savings from the clock change are disputed.", "b"),
            ("a-3", "not_misleading", "Demo note: studies link the spring change to more accidents.", "a"),
        ],
    ),
    (
        "city-cycling",
        (
            "HashTrust demo passage: new cycle lanes always empty a city's shops. "
            "Car parking is what keeps high streets alive."
        ),
        [
            ("a-4", "misleading", "Demo note: shop surveys find cyclists spend as much per month.", "a"),
            (
                "b-3",
                "not_misleading",
                "Demo note: some streets did lose deliveries during construction.",
                "b",
            ),
            ("b-4", "misleading", "Demo note: the passage cites no study for 'always'.", "both"),
        ],
    ),
    (
        "remote-work",
        (
            "HashTrust demo passage: remote workers are always less productive than office workers. "
            "Every manager agrees."
        ),
        [
            ("b-5", "not_misleading", "Demo note: several firms reported fewer hours logged at home.", "b"),
            ("a-1", "misleading", "Demo note: this is wrong, everyone knows it.", "none"),
            ("a-5", "misleading", "Demo note: large surveys find productivity similar or higher.", "a"),
        ],
    ),
]


def email(handle: str) -> str:
    return f"demo-camp-{handle}@{DOMAIN}"


def handles() -> list[str]:
    return [f"{camp}-{n}" for camp in "ab" for n in range(1, CAMP_SIZE + 1)]


def install_id(handle: str) -> uuid.UUID:
    return uuid.uuid5(INSTALL_NAMESPACE, email(handle))


def seed(conn: Connection) -> None:
    hasher = PasswordHasher()
    accounts = {}
    for handle in handles():
        conn.execute(
            "INSERT INTO user_account (email, password_hash) VALUES (%s, %s) ON CONFLICT (email) DO NOTHING",
            (email(handle), hasher.hash(secrets.token_urlsafe(32))),
        )
        accounts[handle] = conn.execute(
            "SELECT id FROM user_account WHERE email = %s", (email(handle),)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO install (id, account_id) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET account_id = %s",
            (install_id(handle), accounts[handle], accounts[handle]),
        )
    for slug, passage, notes in NARRATIVES:
        narrative_id = demo_narrative(conn, slug, passage)
        for author, classification, body, liked_by in notes:
            note_id = demo_note(
                conn, narrative_id, install_id(author), accounts[author], classification, body
            )
            for handle in handles():
                if handle == author:
                    continue
                helpful = liked_by == "both" or handle.startswith(liked_by)
                conn.execute(
                    "INSERT INTO note_rating (note_id, install, account_id, helpfulness) VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (note_id, install) DO NOTHING",
                    (note_id, install_id(handle), accounts[handle], "helpful" if helpful else "not_helpful"),
                )
    score(conn)


def demo_narrative(conn: Connection, slug: str, passage: str) -> int:
    url = URL_PREFIX + slug
    row = conn.execute(
        "SELECT id FROM narrative WHERE source_url = %s AND source = 'note'", (url,)
    ).fetchone()
    if row:
        return row[0]
    narrative_id = conn.execute(
        "INSERT INTO narrative (title, claim, source, source_url, state) VALUES (%s, %s, 'note', %s, 'active')"
        " RETURNING id",
        (passage.split(". ")[0] + ".", passage, url),
    ).fetchone()[0]
    variant_id = conn.execute(
        "INSERT INTO variant (narrative_id) VALUES (%s) RETURNING id", (narrative_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO fingerprint (content_hash, content, variant_id) VALUES (%s, %s, %s)",
        (f"demo-seed-{slug}", passage, variant_id),
    )
    return narrative_id


def demo_note(
    conn: Connection,
    narrative_id: int,
    author: uuid.UUID,
    account_id: uuid.UUID,
    classification: str,
    body: str,
) -> int:
    row = conn.execute(
        "SELECT id FROM note WHERE narrative_id = %s AND body = %s", (narrative_id, body)
    ).fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO note (narrative_id, author_install, account_id, classification, body, moderation)"
        " VALUES (%s, %s, %s, %s, %s, 'visible') RETURNING id",
        (narrative_id, author, account_id, classification, body),
    ).fetchone()[0]


def remove(conn: Connection) -> None:
    narratives = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM narrative WHERE source = 'note' AND source_url LIKE %s", (URL_PREFIX + "%",)
        )
    ]
    accounts = [
        row[0] for row in conn.execute("SELECT id FROM user_account WHERE email LIKE %s", (f"%@{DOMAIN}",))
    ]
    installs = [
        row[0] for row in conn.execute("SELECT id FROM install WHERE account_id = ANY(%s)", (accounts,))
    ]
    notes = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM note WHERE narrative_id = ANY(%s) OR account_id = ANY(%s)", (narratives, accounts)
        )
    ]
    conn.execute("DELETE FROM note_rating WHERE note_id = ANY(%s) OR install = ANY(%s)", (notes, installs))
    conn.execute("DELETE FROM note WHERE id = ANY(%s)", (notes,))
    delete_narratives(conn, narratives)
    conn.execute("DELETE FROM install WHERE id = ANY(%s)", (installs,))
    conn.execute("DELETE FROM user_account WHERE id = ANY(%s)", (accounts,))
    score(conn)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remove", action="store_true", help="delete everything the seed created")
    args = parser.parse_args()
    with psycopg.connect(settings.database_url) as conn:
        (remove if args.remove else seed)(conn)
    print("removed demo rater history" if args.remove else "seeded demo rater history")


if __name__ == "__main__":
    main()
