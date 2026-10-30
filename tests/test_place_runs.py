"""The place rule in a run (#394 decision 5, rows 4 and 5's rerun): the decision rides in the
`test_run` start, a place lost mid-run is rerun at the rule's next place with no `failed`
written, and no place left is a `wait` on `test_place`."""

import json
import textwrap
from datetime import UTC, datetime

import pytest

from orch_it.events.store import Store
from orch_it.testruns.config import Machine, TestSettings
from orch_it.testruns.places import OwnPlace, PlaceRefused, PlaceVanished
from orch_it.testruns.runner import RunError, run_tests

REPO = "acme-x/widgets-y"


def green(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / "test_one.py").write_text(textwrap.dedent("""
        def test_one():
            pass
        """), "utf-8")
    return root


class Vanishing:
    def __init__(self, place, os="linux"):
        self.id, self.os = place, os

    def run(self, request, scratch):
        raise PlaceVanished("the machine went away")


def starts(store):
    return [json.loads(d) for (d,) in store.query(
        "SELECT detail FROM events WHERE kind = 'step.started' AND step = 'test_run' ORDER BY at, id").rows]


def ends(store, step="test_run"):
    return store.query("SELECT outcome, reason FROM events WHERE kind = 'step.ended' AND step = ?"
                       " ORDER BY at, id", (step,)).rows


def test_the_decision_rides_in_the_start_with_every_offered_place(tmp_path):
    store = Store.open(tmp_path / "store")
    own = OwnPlace()
    done = run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(),
                     places={"own": own})
    assert (done.place, done.outcome) == ("own", "passed")
    [start] = starts(store)
    assert (start["chosen"], start["reason"]) == ("own", "nothing_measured")
    assert start["places_offered"] == [
        {"place": "own", "expected_minutes": None, "dropped": None},
        {"place": "github", "expected_minutes": None, "dropped": "place_off"}]


def test_a_place_lost_mid_run_is_rerun_at_the_rule_s_next_place(tmp_path):
    store = Store.open(tmp_path / "store")
    settings = TestSettings(machines=(Machine("box", "box", "linux", "/x"),))
    places = {"own": Vanishing("own"), "machine:box": OwnPlace(id="machine:box", os="linux")}
    done = run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=settings, places=places,
                     need_os="linux")
    assert (done.place, done.outcome) == ("machine:box", "passed")
    assert ends(store) == [("lost", "place_vanished"), ("passed", None)]
    second = starts(store)[1]
    assert second["chosen"] == "machine:box"
    assert {"place": "own", "expected_minutes": None, "dropped": "place_vanished"} in second["places_offered"]


def occupy_own(store):
    """A live run at own, so own (capacity 1) is busy."""

    writer = store.writer()
    envelope = {"repo": REPO, "ticket": None, "role": "script", "session": None, "parent_session": None,
                "turn": None, "step": "test_run", "run": "other", "note": None}
    writer.write(envelope | {"kind": "step.started", "detail": {"place": "own", "bound_minutes": 15}})
    return writer, envelope


class Ticks:
    """A sleep and a clock for a wait: each sleep moves the clock on by its seconds, as a real sleep does."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_no_place_left_waits_on_test_place_and_gives_up_refused(tmp_path):
    store = Store.open(tmp_path / "store")
    occupy_own(store)
    ticks = Ticks()
    done = run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(),
                     places={"own": OwnPlace()}, wait_minutes=1, sleep=ticks.sleep, clock=ticks.clock)
    assert len(ticks.sleeps) == 4  # one minute of 15-second polls on the clock, then it gives up
    assert (done.outcome, done.reason) == ("refused", "busy")
    [(reason,)] = store.query("SELECT reason FROM events WHERE kind = 'step.started' AND step = 'wait'").rows
    assert reason == "test_place"
    assert ends(store, "wait") == [("no_result", None)]
    assert ends(store) == [("refused", "busy")]


class Refusing:
    def __init__(self, place, code):
        self.id, self.os, self.code = place, "linux", code

    def run(self, request, scratch):
        raise PlaceRefused(self.code)


def test_a_place_that_refused_busy_or_has_no_capacity_is_not_waited_for(tmp_path):
    for settings, places in ((TestSettings(), {"own": Refusing("own", "busy")}),
                             (TestSettings(own_capacity=0), {"own": OwnPlace()})):
        store = Store.open(tmp_path / f"store{len(places)}{settings.own_capacity}")
        sleeps = []
        done = run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=settings, places=places,
                         sleep=sleeps.append)
        assert (done.outcome, sleeps) == ("refused", [])
        # no place left still writes its wait on test_place (decision 5), ended at once
        assert store.query("SELECT reason FROM events WHERE step = 'wait' AND kind = 'step.started'").rows == [
            ("test_place",)]
        assert ends(store, "wait") == [("no_result", None)]


def test_a_wait_stopped_by_a_person_is_ended_stopped(tmp_path):
    store = Store.open(tmp_path / "store")
    occupy_own(store)

    def interrupted(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(),
                  places={"own": OwnPlace()}, sleep=interrupted)
    assert ends(store, "wait") == [("stopped", None)]


class Budgeted:
    """A github place for the wait only: its run passes github_tests=False, so the rule does not offer it (the setting's
    default is on), and it reads the request budget GitHub has left, as the real place does."""

    id, os = "github", "linux"

    def __init__(self, remaining):
        self.remaining = remaining

    def rate_limit(self):
        return None if self.remaining is None else (self.remaining, 5000)

    def run(self, request, scratch):
        raise PlaceRefused("busy")


def test_the_wait_polls_at_its_base_while_the_github_budget_is_above_the_threshold(tmp_path):
    store = Store.open(tmp_path / "store")
    occupy_own(store)
    ticks = Ticks()
    run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(github_tests=False),
              places={"own": OwnPlace(), "github": Budgeted(2500)}, wait_minutes=1, sleep=ticks.sleep,
              clock=ticks.clock)
    assert ticks.sleeps == [15.0] * 4  # half of the 5000 left is above the 20% threshold: the base, 15 s


def test_a_wait_with_no_budget_reading_keeps_the_base_interval(tmp_path):
    store = Store.open(tmp_path / "store")
    occupy_own(store)
    ticks = Ticks()
    run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(),
              places={"own": OwnPlace(), "github": Budgeted(None)}, wait_minutes=1, sleep=ticks.sleep,
              clock=ticks.clock)
    assert ticks.sleeps == [15.0] * 4  # GitHub's budget is unread: the interval stays at its base


def test_a_one_minute_wait_at_a_tenth_left_sleeps_at_most_a_minute(tmp_path):
    store = Store.open(tmp_path / "store")
    occupy_own(store)
    ticks = Ticks()
    run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(),
              places={"own": OwnPlace(), "github": Budgeted(500)}, wait_minutes=1, sleep=ticks.sleep,
              clock=ticks.clock)
    assert ticks.sleeps == [60.0]  # a tenth left: the grown 60 s is the whole minute, one poll
    assert sum(ticks.sleeps) <= 60


def test_the_wait_ends_on_the_clock_and_its_last_poll_is_clamped_to_the_time_left(tmp_path):
    store = Store.open(tmp_path / "store")
    occupy_own(store)
    ticks = Ticks()
    done = run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(),
                     places={"own": OwnPlace(), "github": Budgeted(500)}, wait_minutes=1.5, sleep=ticks.sleep,
                     clock=ticks.clock)
    assert ticks.sleeps == [60.0, 30.0]  # 60 s at a tenth left, then the 30 s left of 90 s, not a whole 60
    assert sum(ticks.sleeps) <= 1.5 * 60
    assert (done.outcome, done.reason) == ("refused", "busy")


def test_a_wait_ends_at_its_bound_on_the_clock_even_if_the_store_clock_stands_still(tmp_path):
    store = Store.open(tmp_path / "store")
    occupy_own(store)
    frozen = datetime.now(UTC)  # the occupying run stays live while the store's clock stands still
    ticks = Ticks()
    done = run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(),
                     places={"own": OwnPlace()}, wait_minutes=2, sleep=ticks.sleep, clock=ticks.clock,
                     now=lambda: frozen)
    assert (done.outcome, len(ticks.sleeps), sum(ticks.sleeps)) == ("refused", 8, 120)


def test_an_os_outside_the_set_is_refused_before_anything_is_written(tmp_path):
    store = Store.open(tmp_path / "store")
    with pytest.raises(RunError) as refused:
        run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(), need_os="beos")
    assert refused.value.code == "bad_os"
    assert store.query("SELECT count(*) FROM events").rows == [(0,)]


def test_a_wait_ends_when_a_place_frees_and_the_run_goes_there(tmp_path):
    store = Store.open(tmp_path / "store")
    writer, envelope = occupy_own(store)

    def other_run_ends(_seconds):
        writer.write(envelope | {"kind": "step.ended", "detail": {"outcome": "passed"}})

    done = run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=TestSettings(),
                     places={"own": OwnPlace()}, wait_minutes=5, sleep=other_run_ends)
    assert (done.place, done.outcome) == ("own", "passed")
    assert ends(store, "wait") == [("passed", None)]


def test_a_forced_place_is_taken_and_the_decision_says_forced(tmp_path):
    store = Store.open(tmp_path / "store")
    settings = TestSettings(own_capacity=0)  # the rule would drop own as busy; a human forces it
    done = run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=settings,
                     places={"own": OwnPlace()}, place="own")
    assert (done.place, done.outcome) == ("own", "passed")
    [start] = starts(store)
    assert (start["chosen"], start["reason"]) == ("own", "forced")
    assert start["places_offered"][0] == {"place": "own", "expected_minutes": None, "dropped": "busy"}


def test_the_next_run_is_placed_by_the_measured_minutes_of_the_last(tmp_path):
    store = Store.open(tmp_path / "store")
    settings = TestSettings(machines=(Machine("box", "box", "linux", "/x"),))
    places = {"own": OwnPlace(id="own", os="linux"), "machine:box": OwnPlace(id="machine:box", os="linux")}
    run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=settings, places=places, need_os="linux")
    run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=settings, places=places, need_os="linux")
    first, second = starts(store)
    assert first["reason"] == "nothing_measured"
    assert second["reason"] == "earliest_finish"
    assert all(o["expected_minutes"] is not None for o in second["places_offered"] if not o["dropped"])
