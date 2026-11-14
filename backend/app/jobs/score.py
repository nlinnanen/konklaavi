"""score_notes: decide each note's status by bridging, or by plain agreement while its raters are new.

Bridging is the Community Notes algorithm. Reference: the Community Notes guide
(https://communitynotes.x.com/guide/en/under-the-hood/ranking-notes) and its open-source scorer
(https://github.com/twitter/communitynotes), whose matrix factorisation this is with one factor dimension.

Each counted rating r (helpful 1, somewhat 0.5, not helpful 0) is fit as

    r ~ mu + i_user + i_note + f_user * f_note

minimising the squared error plus LAMBDA_I * (mu^2 + i_user^2 + i_note^2) + LAMBDA_F * (f_user^2 + f_note^2),
intercepts regularised five times as hard as factors as in the paper. The factor takes up the agreement
a shared viewpoint explains, so a note's intercept is the helpfulness raters of every viewpoint agree on:
a note rated helpful by one side only gets a large factor and a low intercept. The fit is alternating
least squares, each parameter set in closed form given the others.

A rater's factor is learned from how their ratings differ from others' across notes, so a rater with
few ratings sits near the centre of the axis and a community of such raters reveals no viewpoints: the
global mean absorbs their agreement and regularisation keeps intercepts low, so bridging never decides.
A note whose counted raters are mostly new, more than half of them with fewer than BRIDGING_MIN_HISTORY
counted ratings on other notes, is decided by agreement instead; the others by bridging.

A rating counts when its install is linked to an account; an account counts once per note, with the
latest rating of its installs. A note with fewer than NOTE_MIN_RATINGS counted ratings needs more ratings
under either rule. Otherwise:

- Bridging: helpful at i_note >= 0.40 with |f_note| < 0.50, not helpful at
  i_note <= -0.05 - 0.8 * |f_note|, controversial at |f_note| >= 0.50, and needs more ratings in between.
- Agreement: helpful when the mean rating is at least AGREEMENT_HELPFUL, not helpful when it is at most
  AGREEMENT_NOT_HELPFUL, and needs more ratings in between. Agreement can't see viewpoints, so it never
  calls a note controversial.

note.decided_by records the rule behind a helpful, not helpful or controversial status.
"""

from dataclasses import dataclass

import numpy as np
from psycopg import Connection

from app.config import settings
from app.jobs import register

VALUES = {"helpful": 1.0, "somewhat": 0.5, "not_helpful": 0.0}
LAMBDA_I = 0.15
LAMBDA_F = 0.03
ITERATIONS = 500
TOLERANCE = 1e-9
HELPFUL_INTERCEPT = 0.40
POLARISED_FACTOR = 0.50
NOT_HELPFUL_INTERCEPT = -0.05
NOT_HELPFUL_SLOPE = 0.8
DECIDED = ("helpful", "not_helpful")


@dataclass(frozen=True)
class Fit:
    mu: float
    note_intercepts: np.ndarray
    note_factors: np.ndarray
    user_intercepts: np.ndarray
    user_factors: np.ndarray


def fit(users: np.ndarray, notes: np.ndarray, ratings: np.ndarray, n_users: int, n_notes: int) -> Fit:
    """Fit the model to ratings[k] of note notes[k] by user users[k], indices from 0."""
    rng = np.random.default_rng(0)
    iu, inote = np.zeros(n_users), np.zeros(n_notes)
    fu, fnote = rng.normal(0, 0.1, n_users), rng.normal(0, 0.1, n_notes)
    mu = 0.0
    per_user = np.bincount(users, minlength=n_users)
    per_note = np.bincount(notes, minlength=n_notes)
    for _ in range(ITERATIONS):
        previous = np.concatenate([[mu], iu, inote, fu, fnote])
        mu = float(
            (ratings - iu[users] - inote[notes] - fu[users] * fnote[notes]).sum() / (len(ratings) + LAMBDA_I)
        )
        residual = ratings - mu - inote[notes] - fu[users] * fnote[notes]
        iu = np.bincount(users, residual, n_users) / (per_user + LAMBDA_I)
        residual = ratings - mu - iu[users] - fu[users] * fnote[notes]
        inote = np.bincount(notes, residual, n_notes) / (per_note + LAMBDA_I)
        base = ratings - mu - iu[users] - inote[notes]
        fu = np.bincount(users, base * fnote[notes], n_users) / (
            np.bincount(users, fnote[notes] ** 2, n_users) + LAMBDA_F
        )
        fnote = np.bincount(notes, base * fu[users], n_notes) / (
            np.bincount(notes, fu[users] ** 2, n_notes) + LAMBDA_F
        )
        if np.abs(np.concatenate([[mu], iu, inote, fu, fnote]) - previous).max() < TOLERANCE:
            break
    return Fit(mu, inote, fnote, iu, fu)


def bridging_status(count: int, intercept: float, factor: float) -> str:
    if count < settings.note_min_ratings:
        return "needs_more_ratings"
    if intercept >= HELPFUL_INTERCEPT and abs(factor) < POLARISED_FACTOR:
        return "helpful"
    if intercept <= NOT_HELPFUL_INTERCEPT - NOT_HELPFUL_SLOPE * abs(factor):
        return "not_helpful"
    if abs(factor) >= POLARISED_FACTOR:
        return "controversial"
    return "needs_more_ratings"


def agreement_status(count: int, mean: float) -> str:
    if count < settings.note_min_ratings:
        return "needs_more_ratings"
    if mean >= settings.agreement_helpful:
        return "helpful"
    if mean <= settings.agreement_not_helpful:
        return "not_helpful"
    return "needs_more_ratings"


def decide(
    count: int, new_raters: int, mean: float, intercept: float, factor: float
) -> tuple[str, str | None]:
    """The note's status and the rule that set it, None while it needs more ratings."""
    if 2 * new_raters > count:
        rule, result = "agreement", agreement_status(count, mean)
    else:
        rule, result = "bridging", bridging_status(count, intercept, factor)
    return result, None if result == "needs_more_ratings" else rule


def score(conn: Connection) -> None:
    """Fit every counted rating and store each note's intercept, factor, status and deciding rule.

    decided_at is set when a note's status becomes helpful or not helpful and cleared when it is undecided.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT ON (r.note_id, i.account_id) r.note_id, i.account_id, r.helpfulness
        FROM note_rating r JOIN install i ON i.id = r.install
        WHERE i.account_id IS NOT NULL
        ORDER BY r.note_id, i.account_id, r.created_at DESC
        """
    ).fetchall()
    note_ids = sorted({note for note, _, _ in rows})
    user_ids = sorted({user for _, user, _ in rows})
    note_index = {id_: k for k, id_ in enumerate(note_ids)}
    user_index = {id_: k for k, id_ in enumerate(user_ids)}
    notes = np.array([note_index[note] for note, _, _ in rows], dtype=int)
    users = np.array([user_index[user] for _, user, _ in rows], dtype=int)
    ratings = np.array([VALUES[helpfulness] for _, _, helpfulness in rows])
    model = fit(users, notes, ratings, len(user_ids), len(note_ids)) if rows else None
    counts = np.bincount(notes, minlength=len(note_ids))
    means = np.bincount(notes, ratings, len(note_ids)) / np.maximum(counts, 1)
    history = np.bincount(users, minlength=len(user_ids))[users] - 1
    new_raters = np.bincount(notes, history < settings.bridging_min_history, len(note_ids))
    scored = [
        (
            id_,
            *decide(
                int(counts[k]),
                int(new_raters[k]),
                float(means[k]),
                model.note_intercepts[k],
                model.note_factors[k],
            ),
            float(model.note_intercepts[k]),
            float(model.note_factors[k]),
        )
        for id_, k in note_index.items()
    ]
    conn.execute(
        """
        WITH s AS (
          SELECT * FROM unnest(%s::bigint[], %s::text[], %s::text[], %s::float8[], %s::float8[])
            AS s(id, status, decided_by, intercept, factor)
        )
        UPDATE note n SET
          intercept = s.intercept, factor = s.factor, status = s.status, decided_by = s.decided_by,
          decided_at = CASE WHEN s.status <> ALL(%s) THEN NULL
                            WHEN s.status = n.status THEN n.decided_at ELSE now() END
        FROM s WHERE s.id = n.id
        """,
        (
            [row[0] for row in scored],
            [row[1] for row in scored],
            [row[2] for row in scored],
            [row[3] for row in scored],
            [row[4] for row in scored],
            list(DECIDED),
        ),
    )
    conn.execute(
        "UPDATE note SET status = 'needs_more_ratings', decided_by = NULL, intercept = NULL, factor = NULL,"
        " decided_at = NULL WHERE NOT (id = ANY(%s)) AND (status <> 'needs_more_ratings' OR intercept IS NOT NULL)",
        (note_ids,),
    )


@register("score_notes")
def handle(conn: Connection, payload: dict) -> None:
    score(conn)
