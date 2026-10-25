"""The place rule in a run (#394 decision 5, rows 4 and 5's rerun): the decision rides in the
`test_run` start, a place lost mid-run is rerun at the rule's next place with no `failed`
written, and no place left is a `wait` on `test_place`."""

import json
import textwrap
from datetime import UTC, datetime

from orch_it.events.store import Store
from orch_it.testruns.config import Machine, TestSettings
from orch_it.testruns.places import OwnPlace, PlaceVanished
from orch_it.testruns.runner import run_tests

REPO = "Rasslabsya4el/orch-it"


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


def test_no_place_left_waits_on_test_place_and_gives_up_refused(tmp_path):
    store = Store.open(tmp_path / "store")
    settings = TestSettings(own_capacity=0)
    done = run_tests(store, work_tree=green(tmp_path), repo=REPO, settings=settings,
                     places={"own": OwnPlace()}, wait_minutes=0, sleep=lambda s: None)
    assert (done.outcome, done.reason) == ("refused", "busy")
    [(reason,)] = store.query("SELECT reason FROM events WHERE kind = 'step.started' AND step = 'wait'").rows
    assert reason == "test_place"
    assert ends(store, "wait") == [("no_result", None)]
    assert ends(store) == [("refused", "busy")]


def test_a_wait_ends_when_a_place_frees_and_the_run_goes_there(tmp_path):
    store = Store.open(tmp_path / "store")
    writer = store.writer()
    envelope = {"repo": REPO, "ticket": None, "role": "script", "session": None, "parent_session": None,
                "turn": None, "step": "test_run", "run": "other", "note": None}
    writer.write(envelope | {"kind": "step.started", "detail": {"place": "own", "bound_minutes": 15}})

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
