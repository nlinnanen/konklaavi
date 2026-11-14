import json
import random

import pytest

import sim


class FakeApi:
    """The API's vote rule: 2+ votes with at least half negative flag a hash. A narrative sentence is
    flagged until `retire_after` trustworthy votes, or for good when that is None."""

    def __init__(self, narrative_hashes=(), retire_after=None):
        self.votes: dict[str, dict[str, str]] = {}
        self.narrative_hashes = set(narrative_hashes)
        self.retire_after = retire_after

    def vote(self, user, item, verdict):
        self.votes.setdefault(item.hash, {})[user] = verdict

    def _flagged(self, hash):
        verdicts = list(self.votes.get(hash, {}).values())
        if hash in self.narrative_hashes:
            trustworthy = verdicts.count("trustworthy")
            return self.retire_after is None or trustworthy < self.retire_after
        negative = sum(v != "trustworthy" for v in verdicts)
        return len(verdicts) >= 2 and 2 * negative >= len(verdicts)

    def flagged(self, hashes):
        return {h for h in hashes if self._flagged(h)}


def test_brigade_needs_as_many_accounts_as_the_honest_votes_it_outweighs():
    assert sim.brigade_to_flag(FakeApi(), 3, [sim.new_user() for _ in range(10)]) == 3


def test_brigade_that_never_flags_within_its_size_needs_more():
    assert sim.brigade_to_flag(FakeApi(), 10, [sim.new_user() for _ in range(5)]) is None


def test_retire_counts_trustworthy_votes_until_the_sentence_is_unflagged():
    api = FakeApi({"n1"}, retire_after=4)

    assert sim.brigade_to_retire(api, "n1", [sim.new_user() for _ in range(10)]) == 4


def test_retire_refuses_a_sentence_that_is_not_flagged():
    with pytest.raises(ValueError):
        sim.brigade_to_retire(FakeApi(), "n1", [sim.new_user()])


def test_honest_crowd_flags_every_false_item_and_no_reliable_one():
    result = sim.population(FakeApi(), random.Random(0), {"honest": 3}, items=5, turnout=1.0)

    assert (result["reliable_flagged"], result["false_flagged"], result["votes"]) == (0.0, 1.0, 30)


def test_partisans_flag_reliable_items_they_outnumber_honest_voters_on():
    result = sim.population(FakeApi(), random.Random(0), {"honest": 1, "partisan": 2}, items=5, turnout=1.0)

    assert result["reliable_flagged"] == 1.0


def test_saving_a_run_keeps_the_runs_of_other_labels(tmp_path):
    path = tmp_path / "simulation.json"

    sim.save(path, "unweighted", {"cap": 1})
    sim.save(path, "weighted", {"cap": 2})
    sim.save(path, "unweighted", {"cap": 3})

    assert json.loads(path.read_text()) == {"runs": {"unweighted": {"cap": 3}, "weighted": {"cap": 2}}}
