import uuid
from datetime import datetime, timedelta

import pytest

from app.trust import (
    BURST_WINDOW,
    CastVote,
    Settlement,
    Tally,
    Vote,
    Voter,
    next_state,
    record,
    reputation,
    weights,
)

T0 = datetime(2026, 11, 1, 12, 0)
NEW = reputation(0, 0)


def vote(verdict, first_seen=T0, rep=NEW):
    return Vote(uuid.uuid4(), verdict, Voter(first_seen, rep))


def spread(verdict, n, rep=NEW):
    """n accounts that first appeared a day apart."""
    return [vote(verdict, T0 + timedelta(days=i), rep) for i in range(n)]


def tally(*votes):
    return Tally.of([v for group in votes for v in group])


class TestReputation:
    def test_new_accounts_start_below_accounts_that_agreed_with_settled_narratives(self):
        assert reputation(0, 0) < reputation(1, 0) < reputation(3, 0)

    def test_disagreement_lowers_reputation_below_a_new_account(self):
        assert reputation(0, 2) < reputation(0, 0)

    def test_reporting_before_confirmation_counts_double(self):
        confirmed = Settlement("narrative:1", bad=True, settled_at=T0)
        early = CastVote("misleading", T0 - timedelta(hours=1), (confirmed,))
        late = CastVote("misleading", T0 + timedelta(hours=1), (confirmed,))
        assert record([early]) == (2, 0)
        assert record([late]) == (1, 0)


class TestWeights:
    def test_votes_weigh_their_voters_reputation(self):
        assert weights(
            [vote("misleading", rep=0.25), vote("trustworthy", T0 + timedelta(days=1), rep=0.7)]
        ) == [
            0.25,
            0.7,
        ]

    def test_a_burst_of_three_new_accounts_on_one_side_weighs_as_one(self):
        burst = [vote("misleading", T0 + BURST_WINDOW * i / 2) for i in range(3)]
        assert sum(weights(burst)) == pytest.approx(NEW)

    def test_accounts_that_first_appeared_further_apart_are_not_a_burst(self):
        apart = [vote("misleading", T0 + BURST_WINDOW * 1.1 * i) for i in range(3)]
        assert weights(apart) == [NEW] * 3

    def test_two_accounts_are_not_a_burst(self):
        assert weights([vote("misleading"), vote("misinformation")]) == [NEW, NEW]

    def test_a_burst_is_counted_per_side(self):
        mixed = [vote("misleading"), vote("misleading"), vote("trustworthy")]
        assert weights(mixed) == [NEW, NEW, NEW]


class TestFlags:
    def test_two_new_accounts_flag_content(self):
        assert tally(spread("misleading", 2)).flags() is True

    def test_one_account_never_flags_content(self):
        assert tally(spread("misleading", 1, rep=0.9)).flags() is False

    def test_a_burst_of_new_accounts_does_not_flag_content(self):
        assert tally([vote("misleading") for _ in range(5)]).flags() is False

    def test_a_reputable_trustworthy_vote_outweighs_two_new_negative_ones(self):
        assert tally(spread("misleading", 2), [vote("trustworthy", rep=reputation(3, 0))]).flags() is False


class TestLifecycle:
    def test_an_euvsdisinfo_case_stays_active_whatever_the_votes(self):
        votes = tally(spread("trustworthy", 5))
        assert next_state("active", "euvsdisinfo", "misinformation", votes) == "active"

    def test_an_active_report_stays_active_without_votes(self):
        assert next_state("active", "report", "misinformation", Tally()) == "active"

    def test_a_report_stays_under_review_with_two_negative_votes_that_disagree_with_its_verdict(self):
        votes = tally(spread("misinformation", 2))
        assert next_state("under_review", "report", "misleading", votes) == "under_review"

    def test_a_report_becomes_active_when_three_users_call_it_negative(self):
        votes = tally(spread("misinformation", 2), spread("manipulative", 1))
        assert next_state("under_review", "report", "misleading", votes) == "active"

    def test_a_report_becomes_active_when_two_users_agree_with_its_analysis_verdict(self):
        votes = tally(spread("misleading", 2))
        assert next_state("under_review", "report", "misleading", votes) == "active"

    def test_a_burst_of_new_accounts_does_not_activate_a_report(self):
        votes = tally([vote("misleading") for _ in range(5)])
        assert next_state("under_review", "report", "misleading", votes) == "under_review"

    def test_mostly_trustworthy_votes_dispute_a_narrative(self):
        votes = tally(spread("trustworthy", 2), spread("misinformation", 1))
        assert next_state("active", "report", "misinformation", votes) == "disputed"

    def test_a_clear_trustworthy_majority_retires_a_narrative(self):
        votes = tally(spread("trustworthy", 4), spread("misinformation", 2))
        assert next_state("disputed", "report", "misinformation", votes) == "retired"

    def test_three_trustworthy_votes_short_of_two_thirds_only_dispute(self):
        votes = tally(spread("trustworthy", 3), spread("misinformation", 2))
        assert next_state("active", "report", "misinformation", votes) == "disputed"

    def test_a_disputed_narrative_returns_when_negative_votes_catch_up(self):
        votes = tally(spread("trustworthy", 2), spread("misinformation", 2))
        assert next_state("disputed", "report", "misinformation", votes) == "active"

    def test_retired_and_unshipped_narratives_keep_their_state(self):
        votes = tally(spread("misinformation", 5))
        assert [next_state(s, "report", "misinformation", votes) for s in ("retired", "candidate")] == [
            "retired",
            "candidate",
        ]
