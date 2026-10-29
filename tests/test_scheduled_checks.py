"""The scheduled checks (#414 items 8 and 3; rows 5 and 9 on fixture GitHub): one tick of `run_scheduled_checks`
runs the bound check and the three Windows checks, `reconcile` runs a tick after the workflows' events are in the
store, a tick twice posts and opens nothing twice, and a failing check is logged while the others still run.
The planted runs sit over the default bounds by fixed steps; a run of N minutes is a passed run of N minutes."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fake_github import FAILED, PASSED, REPO, FakeGitHub
from required_names import LINUX_CHECK, LINUX_OS, WINDOWS_CHECK

from orch_it.events.store import Store
from orch_it.merge import commands, record, scheduled_checks
from orch_it.merge.hub import Result
from orch_it.merge.main_check import check_main
from orch_it.merge.reconcile import reconcile
from orch_it.merge.settings import MergeSettings
from orch_it.testruns.bound_check import default_bounds
from orch_it.testruns.config import windows_thin
from orch_it.testruns.windows_unmarked import post_windows_time

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
TEST = "tests/test_paths.py::test_a_windows_path"
LINUX = default_bounds()["run_minutes"][LINUX_OS]
GREEN = {LINUX_CHECK: PASSED, WINDOWS_CHECK: PASSED}
NIGHTLY = {LINUX_CHECK: PASSED, WINDOWS_CHECK: FAILED}


@pytest.fixture
def world(tmp_path):
    return FakeGitHub(NOW), Store.open(tmp_path / "events")


def envelope(**over):
    base = {"ticket": None, "role": "github", "session": None, "parent_session": None,
            "turn": None, "note": None, "repo": REPO}
    return base | over


def at_minute(minute: float) -> str:
    moment = datetime(2026, 10, 26, 10, 0, tzinfo=UTC) + timedelta(minutes=minute)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def plant_runs(store, start: int, count: int, place: str, os_name: str, minutes: float) -> None:
    """`count` ended passed test runs of `minutes` each, from run number `start`, two minutes apart."""

    with store.writer() as writer:
        for n in range(start, start + count):
            run = f"{place}-{os_name}-{n}"
            began = 2 * n
            writer.write(envelope(kind="step.started", step="test_run", run=run, at=at_minute(began),
                                  detail={"place": place, "run_os": os_name}))
            writer.write(envelope(kind="step.ended", step="test_run", run=run, at=at_minute(began + 1),
                                  detail={"outcome": "passed", "run_minutes": minutes, "reason": None}))


def tick(store, hub, role="github"):
    return scheduled_checks.run_scheduled_checks(store, hub, REPO, {}, "tick", role)


def triage(hub):
    return hub.issues_with_label(REPO, "needs-triage")


def night(hub):
    """A nightly run of `tests` on main: the Windows job fails TEST, Linux passes it, the marker is absent."""

    hub.later(days=1)
    results = [Result(WINDOWS_CHECK, TEST, "failed", False), Result(LINUX_CHECK, TEST, "passed", False)]
    return hub.main_run(hub.land(), NIGHTLY, event="schedule", results=results)


def per_change_green(hub):
    """A green push run of `tests` on main whose reports lack TEST: Linux runs other tests, the thin Windows job
    takes only a marked one."""

    hub.later(minutes=10)
    results = [Result(LINUX_CHECK, "tests/test_other.py::test_linux", "passed", False),
               Result(WINDOWS_CHECK, "tests/test_other.py::test_marked", "passed", True)]
    return hub.main_run(hub.land(), GREEN, event="push", results=results)


def green_night_holding_test(hub):
    """A green nightly run of `tests` on main whose reports hold TEST passed in both jobs."""

    hub.later(days=1)
    results = [Result(LINUX_CHECK, TEST, "passed", False), Result(WINDOWS_CHECK, TEST, "passed", False)]
    return hub.main_run(hub.land(), GREEN, event="schedule", results=results)


def test_a_planted_slow_history_gives_one_ticket_with_the_numbers_after_one_tick(world):
    hub, store = world
    plant_runs(store, 0, 10, "own", LINUX_OS, LINUX + 3)
    report = tick(store, hub)
    assert len(report.bound.opened) == 1 and report.bound.commented == []
    [number] = triage(hub)
    body = hub.issues[(REPO, number)].body
    assert f"{LINUX + 3:.1f} min" in body and f"{LINUX:g} min" in body


def test_a_second_crossing_adds_a_comment_and_no_ticket_and_a_second_tick_adds_nothing(world):
    hub, store = world
    plant_runs(store, 0, 10, "own", LINUX_OS, LINUX + 3)
    [number] = tick(store, hub).bound.opened
    plant_runs(store, 10, 10, "own", LINUX_OS, LINUX + 3.5)
    report = tick(store, hub)
    assert report.bound.opened == [] and report.bound.commented == [number]
    assert triage(hub) == [number]
    assert len(hub.comments(REPO, number)) == 1
    again = tick(store, hub)  # the same data again: no new ticket, no new comment
    assert again.bound.opened == [] and again.bound.commented == []
    assert triage(hub) == [number] and len(hub.comments(REPO, number)) == 1


def test_a_nightly_with_an_unmarked_windows_failure_gives_one_ticket_naming_the_test_and_a_green_per_change_run_leaves_it_open(world):  # noqa: E501
    hub, store = world
    night(hub)
    report = tick(store, hub)
    [number] = triage(hub)
    assert report.unmarked.opened == [number]
    assert TEST in hub.issues[(REPO, number)].title and TEST in hub.issues[(REPO, number)].body
    per_change_green(hub)
    check_main(hub, store, REPO, "main")
    assert hub.issues[(REPO, number)].open and triage(hub) == [number]


def test_a_second_tick_on_the_same_nightly_opens_no_second_ticket_and_posts_no_second_comment(world):
    hub, store = world
    night(hub)
    tick(store, hub)
    [number] = triage(hub)
    again = tick(store, hub)
    assert again.unmarked.opened == [] and again.unmarked.commented == []
    assert triage(hub) == [number] and hub.comments(REPO, number) == []


def test_a_failing_check_is_logged_and_the_next_checks_still_run(world, monkeypatch):
    hub, store = world
    night(hub)

    def broken(*args, **kwargs):
        raise RuntimeError("the hub is down for one call")

    monkeypatch.setattr(scheduled_checks, "run_bound_check", broken)
    report = tick(store, hub)
    assert len(triage(hub)) == 1 and report.unmarked.opened  # the unmarked check ran after the bound check failed
    assert len(report.failed) == 1 and "bound" in report.failed[0]
    [row] = store.query("SELECT detail FROM events WHERE kind = 'error.raised'").rows
    assert json.loads(row[0])["component"] == "scheduled_checks"


THIN = windows_thin({}, REPO)


def windows_run(store, n: int, minutes: float, selection: str) -> None:
    """An ended GitHub test run on the thin job's OS whose pytest took `minutes`; `selection` is `full` or `nightly`."""

    run = f"gh-{THIN.os}-{selection}-{n}"
    with store.writer() as writer:
        writer.write(envelope(kind="step.started", step="test_run", run=run, at=at_minute(10 * n),
                              detail={"place": "github", "run_os": THIN.os, "selection": selection}))
        writer.write(envelope(kind="step.ended", step="test_run", run=run, at=at_minute(10 * n + 2),
                              detail={"outcome": "passed", "run_minutes": minutes, "reason": None}))


def test_the_nightly_time_is_a_comment_on_an_open_time_ticket_once_per_tick(world):
    hub, store = world
    for n in range(3):  # three per-change runs over the target: the time ticket opens
        windows_run(store, n, THIN.change_minutes + 1, "full")
    tick(store, hub)
    [number] = triage(hub)
    windows_run(store, 10, 14.5, "nightly")
    tick(store, hub)
    [comment] = hub.comments(REPO, number)
    assert "14.5" in comment.body
    tick(store, hub)  # the same nightly run again: nothing new
    assert len(hub.comments(REPO, number)) == 1


def test_reconcile_runs_the_scheduled_checks_after_the_store_is_filled(world):
    hub, store = world
    plant_runs(store, 0, 10, "own", LINUX_OS, LINUX + 3)
    done = reconcile(hub, store, REPO, "main", settings=MergeSettings(), now=NOW, raw_settings={})
    assert len(triage(hub)) == 1
    assert done.checks is not None and len(done.checks.bound.opened) == 1


def down(*args, **kwargs):
    raise RuntimeError("the hub is down for one call")


def test_a_red_main_ticket_stays_open_on_a_green_run_without_the_test_and_closes_on_a_green_run_holding_it(world):
    hub, store = world
    night(hub)
    [number] = check_main(hub, store, REPO, "main").opened
    per_change_green(hub)
    check_main(hub, store, REPO, "main")
    assert hub.issues[(REPO, number)].open
    green_night_holding_test(hub)
    check_main(hub, store, REPO, "main")
    assert not hub.issues[(REPO, number)].open


def test_a_failing_check_names_the_tick_role_on_its_error_record(world, monkeypatch):
    hub, store = world
    night(hub)
    real_write = record.write
    roles = []  # the role each error record is written under; "custom-role" is not a closed-set code, so none is stored

    def spy(store_, repo, ticket, role, run, step, events):
        if any(kind == "error.raised" for kind, _ in events):
            roles.append(role)
            return None
        return real_write(store_, repo, ticket, role, run, step, events)

    monkeypatch.setattr(record, "write", spy)
    monkeypatch.setattr(scheduled_checks, "run_bound_check", down)
    tick(store, hub, role="custom-role")
    assert roles == ["custom-role"]


def test_reconcile_writes_its_error_record_under_its_own_role(world, monkeypatch):
    hub, store = world
    monkeypatch.setattr(scheduled_checks, "run_bound_check", down)
    reconcile(hub, store, REPO, "main", settings=MergeSettings(), now=NOW, raw_settings={}, role="orch")
    [row] = store.query("SELECT role FROM events WHERE kind = 'error.raised'").rows
    assert row[0] == "orch"


def test_a_store_that_refuses_the_error_record_does_not_stop_the_next_check(world, monkeypatch):
    hub, store = world
    night(hub)
    real_write = record.write

    def refusing(store_, repo, ticket, role, run, step, events):
        if any(kind == "error.raised" for kind, _ in events):
            raise OSError("the store refuses the error record")
        return real_write(store_, repo, ticket, role, run, step, events)

    monkeypatch.setattr(record, "write", refusing)
    monkeypatch.setattr(scheduled_checks, "run_bound_check", down)
    report = tick(store, hub)
    assert report.unmarked.opened and len(report.failed) == 1 and "bound" in report.failed[0]


def test_post_windows_time_twice_opens_one_ticket_and_posts_no_comment_the_second_time(world):
    hub, store = world
    for n in range(3):
        windows_run(store, n, THIN.change_minutes + 1, "full")
    first = post_windows_time(store, hub, {"repo": REPO})
    assert first.opened
    [number] = triage(hub)
    second = post_windows_time(store, hub, {"repo": REPO})
    assert not second.opened
    assert triage(hub) == [number] and hub.comments(REPO, number) == []


def test_reconcile_prints_the_time_lines_even_when_no_time_ticket_is_open(world, monkeypatch):
    hub, store = world
    for n in range(3):  # under the target: the per-change line says so, and no ticket opens
        windows_run(store, n, THIN.change_minutes / 2, "full")
    windows_run(store, 10, 14.5, "nightly")
    monkeypatch.setattr(commands, "GhPullHub", lambda: hub)
    monkeypatch.setattr(commands.settings, "load", lambda: {})
    args = SimpleNamespace(repo=REPO, base="main", role="script")
    out = commands._reconcile(args, lambda: store, lambda _: REPO, NOW)
    assert triage(hub) == []
    assert "not each over" in out and "took 14.5 min of pytest" in out
