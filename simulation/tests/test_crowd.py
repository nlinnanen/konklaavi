import crowd


class FakeStack:
    """Records votes and confirmations in order; every user's /me answers the same."""

    def __init__(self):
        self.events = []
        self.reports = 0

    def vote(self, user, content_hash, url):
        self.events.append(("vote", user, content_hash))

    def me(self, user):
        return {"reputation": 0.25, "weight": 0.25, "agreed": 0, "disagreed": 0}

    def file_report(self, case):
        self.reports += 1
        return self.reports, f"report-{case.narrative_id}"

    def attach(self, narrative_id, content_hash):
        pass

    def confirm(self, narrative_id, case):
        self.events.append(("confirm", narrative_id, case.narrative_id))


def test_a_report_is_confirmed_delay_rounds_after_every_early_reporter_voted_on_it():
    stack = FakeStack()
    cases = [crowd.Case(10 + i, f"case {i}", f"s{i}") for i in range(3)]

    result = crowd.simulate(
        stack,
        cases,
        ["https://a.example/1", "https://a.example/2", "https://a.example/3"],
        {"early": 2, "late": 1, "bots": 1},
        delay=1,
    )

    assert [(s["round"], s["case"]) for s in result["settlements"]] == [(2, 10), (3, 11)]
    for report, case in [(1, 10), (2, 11)]:
        confirmed = stack.events.index(("confirm", report, case))
        early_votes = [i for i, e in enumerate(stack.events) if e[0] == "vote" and e[2] == f"report-{case}"]
        assert len(early_votes) == 2 and max(early_votes) < confirmed


def weights(*rows):
    return [
        {"round": r, "groups": {"early": {"weight": e}, "late": {"weight": lt}, "bots": {"weight": b}}}
        for r, (e, lt, b) in enumerate(rows)
    ]


def test_bots_fall_below_when_they_stay_under_the_weaker_honest_group():
    rows = weights((0.25, 0.25, 0.25), (0.25, 0.4, 0.2), (0.25, 0.5, 0.3), (0.5, 0.5, 0.1))

    assert crowd.bots_below_honest_after(rows) == 3


def test_bots_that_end_above_an_honest_group_never_fall_below():
    assert crowd.bots_below_honest_after(weights((0.25, 0.25, 0.25), (0.2, 0.4, 0.3))) is None
