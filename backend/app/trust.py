"""Vote weighting and the signature lifecycle.

A vote weighs its voter's reputation, which grows with agreement with settled narratives: active
EUvsDisinfo cases and retired narratives. With `a` agreements and `d` disagreements, counted once per
narrative, reputation is (a + 1) / (a + d + 4): a new account weighs 0.25, three agreements make 0.57.

Accounts that first voted within BURST_WINDOW of each other and vote the same side on the same content
form a burst. A burst of k >= BURST_MIN accounts shares one vote: each weighs reputation / k.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import Connection

VERDICTS = ("misinformation", "manipulative", "misleading", "trustworthy")
NEGATIVE_VERDICTS = VERDICTS[:3]

PRIOR_AGREE, PRIOR_DISAGREE = 1, 3
BURST_WINDOW = timedelta(minutes=10)
BURST_MIN = 3

# Share of the weight that must be negative to flag content, as in client/lib/aggregate.ts.
FLAG_THRESHOLD = 0.5
# One user is never enough to flag content on votes alone.
MIN_VOTES = 2
# Two new accounts; a burst of new accounts weighs less.
MIN_WEIGHT = 0.5

# Shipped narrative states. A disputed narrative ships as under review.
SHIPPED_STATES = ("active", "under_review", "disputed")
# Narratives whose verdict is settled, which reputation is measured against.
SETTLED = "(n.source = 'euvsdisinfo' AND n.state = 'active') OR n.state = 'retired'"


def reputation(agreed: int, disagreed: int) -> float:
    return (agreed + PRIOR_AGREE) / (agreed + disagreed + PRIOR_AGREE + PRIOR_DISAGREE)


@dataclass(frozen=True)
class Voter:
    first_seen: datetime
    reputation: float


@dataclass(frozen=True)
class Vote:
    """One user's vote on one piece of content."""

    user_id: UUID
    verdict: str
    voter: Voter


def weights(votes: list[Vote]) -> list[float]:
    """The weight of each vote on one piece of content."""
    result = [vote.voter.reputation for vote in votes]
    for negative in (True, False):
        side = sorted(
            (i for i, vote in enumerate(votes) if (vote.verdict != "trustworthy") == negative),
            key=lambda i: votes[i].voter.first_seen,
        )
        start = 0
        while start < len(side):
            end = start
            while end < len(side) and (
                votes[side[end]].voter.first_seen - votes[side[start]].voter.first_seen <= BURST_WINDOW
            ):
                end += 1
            burst = side[start:end]
            if len(burst) >= BURST_MIN:
                for i in burst:
                    result[i] /= len(burst)
            start = end
    return result


@dataclass
class Tally:
    """Votes on one piece of content: distinct users and their weight per verdict."""

    users: dict[str, int] = field(default_factory=lambda: dict.fromkeys(VERDICTS, 0))
    weight: dict[str, float] = field(default_factory=lambda: dict.fromkeys(VERDICTS, 0.0))

    @classmethod
    def of(cls, votes: list[Vote]) -> "Tally":
        tally = cls()
        for vote, weight in zip(votes, weights(votes), strict=True):
            tally.users[vote.verdict] += 1
            tally.weight[vote.verdict] += weight
        return tally

    @property
    def total_users(self) -> int:
        return sum(self.users.values())

    @property
    def total_weight(self) -> float:
        return sum(self.weight.values())

    @property
    def negative_users(self) -> int:
        return sum(self.users[v] for v in NEGATIVE_VERDICTS)

    @property
    def negative_weight(self) -> float:
        return sum(self.weight[v] for v in NEGATIVE_VERDICTS)

    def mostly_negative(self) -> bool:
        return self.total_weight > 0 and self.negative_weight >= FLAG_THRESHOLD * self.total_weight

    def flags(self) -> bool:
        return self.total_users >= MIN_VOTES and self.total_weight >= MIN_WEIGHT and self.mostly_negative()

    def top_verdict(self, among: tuple[str, ...] = VERDICTS) -> str:
        return max(among, key=lambda v: (self.weight[v], self.users[v]))


def voters(conn: Connection, user_ids: Iterable[UUID]) -> dict[UUID, Voter]:
    """Each user's first vote time and reputation."""
    rows = conn.execute(
        f"""
        WITH settled AS (
          SELECT v.user_id, n.id AS narrative_id, (n.state = 'active') = (v.verdict <> 'trustworthy') AS agrees
          FROM vote v
          JOIN sentence s ON s.hash = ANY(v.sentence_hashes)
          JOIN fingerprint f ON f.id = s.fingerprint_id
          JOIN variant va ON va.id = f.variant_id
          JOIN narrative n ON n.id = va.narrative_id
          WHERE v.user_id = ANY(%(users)s) AND ({SETTLED})
        ), first AS (
          SELECT user_id, min(received_at) AS first_seen FROM vote WHERE user_id = ANY(%(users)s) GROUP BY 1
        )
        SELECT first.user_id, first.first_seen,
               count(DISTINCT s.narrative_id) FILTER (WHERE s.agrees),
               count(DISTINCT s.narrative_id) FILTER (WHERE NOT s.agrees)
        FROM first LEFT JOIN settled s USING (user_id)
        GROUP BY 1, 2
        """,
        {"users": list(set(user_ids))},
    ).fetchall()
    return {
        user: Voter(first_seen, reputation(agreed, disagreed)) for user, first_seen, agreed, disagreed in rows
    }


def tallies(conn: Connection, rows: list[tuple[str, UUID, str]]) -> dict[str, Tally]:
    """Tallies keyed by content from (content key, user id, verdict) rows, one per user and content."""
    known = voters(conn, (user for _, user, _ in rows))
    votes: dict[str, list[Vote]] = {}
    for key, user, verdict in rows:
        votes.setdefault(key, []).append(Vote(user, verdict, known[user]))
    return {key: Tally.of(content) for key, content in votes.items()}


def next_state(state: str, source: str, verdict: str, tally: Tally) -> str:
    """A shipped narrative's state given the weighted votes on its text.

    Retired: a clear majority calls it trustworthy. Disputed: mostly trustworthy. Active: confirmed by 3
    users' votes, or by 2 users agreeing with the analysis verdict. EUvsDisinfo cases stay active: votes
    are weighed against them, so votes cannot overturn them.
    """
    if state not in SHIPPED_STATES or source == "euvsdisinfo":
        return state
    trustworthy, negative = tally.weight["trustworthy"], tally.negative_weight
    if tally.users["trustworthy"] >= 3 and trustworthy >= MIN_WEIGHT and trustworthy >= 2 * negative:
        return "retired"
    if tally.users["trustworthy"] >= 2 and trustworthy >= MIN_WEIGHT and trustworthy > negative:
        return "disputed"
    confirmed = (
        negative >= MIN_WEIGHT
        and negative >= trustworthy
        and (tally.negative_users >= 3 or tally.users[verdict] >= 2)
    )
    if state == "active" or confirmed:
        return "active"
    return "under_review"


def narrative_tally(conn: Connection, narrative_id: int) -> Tally:
    """Weighted votes on the narrative: each user's latest vote attached to it or on one of its sentences."""
    rows = conn.execute(
        """
        WITH hashes AS (
          SELECT coalesce(array_agg(s.hash), '{}') AS hashes FROM sentence s
          JOIN fingerprint f ON f.id = s.fingerprint_id
          JOIN variant va ON va.id = f.variant_id
          WHERE va.narrative_id = %(id)s
        )
        SELECT DISTINCT ON (v.user_id) '', v.user_id, v.verdict
        FROM vote v, hashes
        WHERE v.narrative_id = %(id)s OR v.sentence_hashes && hashes.hashes
        ORDER BY v.user_id, v.received_at DESC
        """,
        {"id": narrative_id},
    ).fetchall()
    return tallies(conn, rows).get("", Tally())


def vote_narratives(conn: Connection, vote_id: int) -> list[int]:
    """The narratives a vote counts towards."""
    rows = conn.execute(
        """
        SELECT va.narrative_id FROM vote v
        JOIN sentence s ON s.hash = ANY(v.sentence_hashes)
        JOIN fingerprint f ON f.id = s.fingerprint_id
        JOIN variant va ON va.id = f.variant_id
        WHERE v.id = %(id)s AND va.narrative_id IS NOT NULL
        UNION
        SELECT narrative_id FROM vote WHERE id = %(id)s AND narrative_id IS NOT NULL
        """,
        {"id": vote_id},
    ).fetchall()
    return [row[0] for row in rows]


def update_states(conn: Connection, narrative_ids: Iterable[int]) -> None:
    """Move each narrative to the state its votes call for."""
    for narrative_id in sorted(narrative_ids):
        state, source, verdict = conn.execute(
            "SELECT state, source, verdict FROM narrative WHERE id = %s FOR UPDATE", (narrative_id,)
        ).fetchone()
        new = next_state(state, source, verdict, narrative_tally(conn, narrative_id))
        if new != state:
            conn.execute(
                "UPDATE narrative SET state = %s, updated_at = now() WHERE id = %s", (new, narrative_id)
            )
