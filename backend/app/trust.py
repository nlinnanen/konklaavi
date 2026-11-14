"""Vote weighting and the signature lifecycle.

A voter is an account when the voting install is linked to one, counting the votes of all its installs,
and the install otherwise. A vote weighs its voter's reputation, which grows with agreement with settled
content. Settled-bad: active EUvsDisinfo and fact-checker narratives, and report narratives confirmed by
one of them. Settled-good: retired narratives, and pages of reliable outlets (app/reliable.py).

A negative vote agrees with settled-bad content and a trustworthy vote with settled-good content; the
opposite disagrees, so flagging a Reuters page costs reputation. Agreement is counted once per narrative,
or per content on a reliable page. A negative vote cast before its report narrative was confirmed counts
as 2 agreements, which rewards reporting new misinformation early. With `a` agreements and `d`
disagreements, reputation is (a + 1) / (a + d + 4): a new voter weighs 0.25, three agreements make 0.57.

Voters who first voted within BURST_WINDOW of each other and vote the same side on the same content
form a burst. A burst of k >= BURST_MIN voters shares one vote: each weighs reputation / k.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import Connection

from app.reliable import is_reliable_url

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
# Sources whose active narratives are settled-bad on their own authority.
AUTHORITATIVE_SOURCES = ("euvsdisinfo", "factcheck")
# Narratives whose verdict is settled: settled-bad when active, settled-good when retired.
SETTLED = (
    "(n.state = 'active' AND (n.source = ANY(%(authoritative)s) OR n.settled_by IS NOT NULL))"
    " OR n.state = 'retired'"
)


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


@dataclass(frozen=True)
class Settlement:
    """Settled content a vote touches: a narrative, or a page of a reliable outlet."""

    key: str
    bad: bool
    # When a report narrative was confirmed; None for content settled from the start.
    settled_at: datetime | None = None


@dataclass(frozen=True)
class CastVote:
    verdict: str
    received_at: datetime
    settled: tuple[Settlement, ...]


def record(votes: Iterable[CastVote]) -> tuple[int, int]:
    """A voter's agreements and disagreements with settled content, each counted once per content."""
    agreed: dict[str, int] = {}
    disagreed: set[str] = set()
    for vote in votes:
        negative = vote.verdict != "trustworthy"
        for settled in vote.settled:
            if negative != settled.bad:
                disagreed.add(settled.key)
                continue
            early = negative and settled.settled_at is not None and vote.received_at < settled.settled_at
            agreed[settled.key] = max(agreed.get(settled.key, 0), 2 if early else 1)
    return sum(agreed.values()), len(disagreed)


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


@dataclass(frozen=True)
class Standing:
    """An install's voter, its account when linked, and that voter's record."""

    identity: UUID
    first_seen: datetime | None
    agreed: int
    disagreed: int

    @property
    def reputation(self) -> float:
        return reputation(self.agreed, self.disagreed)


def standings(conn: Connection, user_ids: Iterable[UUID]) -> dict[UUID, Standing]:
    """The standing of each install's voter, counting the votes of all installs of a linked account."""
    users = list(set(user_ids))
    identities = dict(
        conn.execute(
            "SELECT u, coalesce(i.account_id, u) FROM unnest(%s::uuid[]) u LEFT JOIN install i ON i.id = u",
            (users,),
        ).fetchall()
    )
    rows = conn.execute(
        f"""
        WITH members AS (
          SELECT * FROM unnest(%(installs)s::uuid[], %(identities)s::uuid[]) AS m(install, identity)
          UNION
          SELECT id, account_id FROM install WHERE account_id = ANY(%(identities)s)
        ), votes AS (
          SELECT m.identity, v.* FROM vote v JOIN members m ON v.user_id = m.install
        ), touched AS (
          SELECT vs.id, va.narrative_id FROM votes vs
          JOIN sentence s ON s.hash = ANY(vs.sentence_hashes)
          JOIN fingerprint f ON f.id = s.fingerprint_id
          JOIN variant va ON va.id = f.variant_id
          WHERE va.narrative_id IS NOT NULL
          UNION
          SELECT id, narrative_id FROM votes WHERE narrative_id IS NOT NULL
        )
        SELECT vs.identity, vs.id, vs.verdict, vs.url, vs.content_hash, vs.received_at,
               n.id, n.state = 'active', n.settled_at
        FROM votes vs
        LEFT JOIN touched t ON t.id = vs.id
        LEFT JOIN narrative n ON n.id = t.narrative_id AND ({SETTLED})
        """,
        {
            "installs": list(identities),
            "identities": list(identities.values()),
            "authoritative": list(AUTHORITATIVE_SOURCES),
        },
    ).fetchall()
    votes: dict[UUID, dict[int, CastVote]] = {}
    for identity, vote_id, verdict, url, content_hash, received_at, narrative_id, bad, settled_at in rows:
        cast = votes.setdefault(identity, {}).get(vote_id)
        if cast is None:
            page = (Settlement(f"page:{content_hash}", bad=False),) if is_reliable_url(url) else ()
            cast = CastVote(verdict, received_at, page)
        if narrative_id is not None:
            settled = Settlement(f"narrative:{narrative_id}", bad, settled_at if bad else None)
            cast = CastVote(verdict, received_at, (*cast.settled, settled))
        votes[identity][vote_id] = cast
    result = {}
    for user, identity in identities.items():
        cast = list(votes.get(identity, {}).values())
        agreed, disagreed = record(cast)
        first_seen = min((vote.received_at for vote in cast), default=None)
        result[user] = Standing(identity, first_seen, agreed, disagreed)
    return result


def tallies(conn: Connection, rows: list[tuple[str, UUID, str]]) -> dict[str, Tally]:
    """Tallies keyed by content from (content key, user id, verdict) rows, one per user and content.

    Of several installs of one account voting on the same content, the first row counts.
    """
    known = standings(conn, (user for _, user, _ in rows))
    votes: dict[str, dict[UUID, Vote]] = {}
    for key, user, verdict in rows:
        standing = known[user]
        voter = Voter(standing.first_seen, standing.reputation)
        votes.setdefault(key, {}).setdefault(standing.identity, Vote(standing.identity, verdict, voter))
    return {key: Tally.of(list(content.values())) for key, content in votes.items()}


def next_state(state: str, source: str, verdict: str, tally: Tally) -> str:
    """A shipped narrative's state given the weighted votes on its text.

    Retired: a clear majority calls it trustworthy. Disputed: mostly trustworthy. Active: confirmed by 3
    users' votes, or by 2 users agreeing with the analysis verdict. EUvsDisinfo and fact-checker
    narratives stay active: votes are weighed against them, so votes cannot overturn them.
    """
    if state not in SHIPPED_STATES or source in AUTHORITATIVE_SOURCES:
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


def narrative_tallies(conn: Connection, narrative_ids: Iterable[int]) -> dict[int, Tally]:
    """Weighted votes per narrative: each user's latest vote on one of its sentences or attached to it.

    A vote is attached to the narrative its report extends or created, or to the narrative of the match
    it gives feedback on.
    """
    ids = list(set(narrative_ids))
    rows = conn.execute(
        """
        WITH hashes AS (
          SELECT va.narrative_id, array_agg(s.hash) AS hashes FROM sentence s
          JOIN fingerprint f ON f.id = s.fingerprint_id
          JOIN variant va ON va.id = f.variant_id
          WHERE va.narrative_id = ANY(%(ids)s)
          GROUP BY 1
        ), ids AS (
          SELECT id, coalesce(h.hashes, '{}') AS hashes FROM unnest(%(ids)s::bigint[]) id
          LEFT JOIN hashes h ON h.narrative_id = id
        )
        SELECT DISTINCT ON (ids.id, v.user_id) ids.id, v.user_id, v.verdict
        FROM ids JOIN vote v ON v.narrative_id = ids.id OR v.sentence_hashes && ids.hashes
        ORDER BY ids.id, v.user_id, v.received_at DESC
        """,
        {"ids": ids},
    ).fetchall()
    return tallies(conn, rows)


def narrative_tally(conn: Connection, narrative_id: int) -> Tally:
    return narrative_tallies(conn, [narrative_id]).get(narrative_id, Tally())


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
    """Move each narrative to the state its votes call for; a settled report narrative keeps its state."""
    for narrative_id in sorted(narrative_ids):
        state, source, verdict, settled_at = conn.execute(
            "SELECT state, source, verdict, settled_at FROM narrative WHERE id = %s FOR UPDATE",
            (narrative_id,),
        ).fetchone()
        if settled_at is not None:
            continue
        new = next_state(state, source, verdict, narrative_tally(conn, narrative_id))
        if new != state:
            conn.execute(
                "UPDATE narrative SET state = %s, updated_at = now() WHERE id = %s", (new, narrative_id)
            )
