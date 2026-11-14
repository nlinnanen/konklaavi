import threading
import time

from crowd.config import Config, Persona, Site
from crowd.runner import Bot, Runner
from crowd.state import State


class Busy(Runner):
    """A runner whose actions only take time and credits, recording when each persona's ran."""

    def __init__(self, tmp_path, personas, workers, credits_per_action=0.0):
        config = Config(
            domain="x.test",
            sites={"s": Site([], False, [])},
            personas=[Persona(name, "casual_reader", False, ["s"], 36000, 36000) for name in personas],
        )
        state = State(tmp_path / "state.json", "http://x.test")
        super().__init__(config, None, state, None, None, tmp_path / "log", workers)
        self.spans: list[tuple[str, float, float]] = []
        self.lock = threading.Lock()
        self.cost = credits_per_action

    def _act(self, bot):
        start = time.monotonic()
        time.sleep(0.05)
        self._charge(self.cost)
        with self.lock:
            self.spans.append((bot.persona.name, start, time.monotonic()))

    _write = _rate = _act


def overlapping(a, b):
    return a[1] < b[2] and b[1] < a[2]


def test_personas_act_at_once_but_each_one_acts_in_turn(tmp_path):
    runner = Busy(tmp_path, ["a", "b", "c"], workers=8)
    runner.run(minutes=2 / 60, budget=100)
    spans = runner.spans
    same = [(x, y) for i, x in enumerate(spans) for y in spans[i + 1 :] if x[0] == y[0]]
    across = [(x, y) for i, x in enumerate(spans) for y in spans[i + 1 :] if x[0] != y[0]]
    assert same and not any(overlapping(x, y) for x, y in same)
    assert any(overlapping(x, y) for x, y in across)


def test_no_action_starts_once_the_budget_is_reached(tmp_path):
    runner = Busy(tmp_path, [str(i) for i in range(6)], workers=2, credits_per_action=1.0)
    runner.run(minutes=1 / 60, budget=3)
    # The two in flight when the third credit lands may finish.
    assert 3 <= runner.credits <= 4
    assert State(tmp_path / "state.json", "").data["credits"] == runner.credits


def test_a_troll_backs_bad_actors_and_votes_down_honest_notes_on_accurate_outlets(tmp_path):
    config = Config(
        domain="x.test",
        sites={"bbc": Site([], True, ["bbc.co.uk"]), "rt": Site([], False, ["rt.com"])},
        personas=[],
    )
    state = State(tmp_path / "state.json", "http://x.test")
    state.data["personas"] = {
        "grim": {"behaviour": "troll", "notes": [{"id": "own"}]},
        "vlad": {"behaviour": "brigade", "notes": [{"id": "brigade-on-bbc"}]},
        "maija": {"behaviour": "careful_fact_checker", "notes": [{"id": "honest-on-rt"}]},
    }
    runner = Runner(config, None, state, None, None, tmp_path / "log")
    bot = Bot(Persona("grim", "troll", True, ["bbc"], 1, 1), state.data["personas"]["grim"], 0, 0)
    notes = [
        {"id": "brigade-on-bbc", "url": "https://www.bbc.co.uk/news/1"},
        {"id": "honest-on-rt", "url": "https://www.rt.com/news/2"},
        {"id": "reader-on-bbc", "url": "https://www.bbc.co.uk/news/3"},
    ]
    assert dict(runner._troll_ratings(bot, notes, 10)) == {
        "brigade-on-bbc": "helpful",
        "reader-on-bbc": "not_helpful",
    }
