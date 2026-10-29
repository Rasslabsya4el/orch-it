"""The bound check (#414 item 8; acceptance row 9): a place's median run time, or a GitHub place's median queue
wait, over its last ten runs, past its bound, opens one `needs-triage` ticket with the numbers; a later crossing
of the same repo, place, OS and kind comments on that open ticket and opens no new one. Every number below is
worked by hand from the planted runs: a run of N minutes is a passed run whose `run_minutes` is N."""

from datetime import UTC, datetime, timedelta

import pytest
from fake_github import REPO, FakeGitHub

from orch_it.events.store import Store
from orch_it.testruns.bound_check import run_bound_check

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
SETTINGS = {"repo": REPO}


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


def runs(start: int, count: int, place: str, os_name: str, minutes: float, *, queue_minutes=None,
         outcome="passed"):
    """`count` ended test runs from run number `start`, two minutes apart; a queue wait when named."""

    events = []
    for n in range(start, start + count):
        run = f"{place}-{os_name}-{n}"
        began = 2 * n
        events.append(envelope(kind="step.started", step="test_run", run=run, at=at_minute(began),
                               detail={"place": place, "run_os": os_name}))
        events.append(envelope(kind="step.ended", step="test_run", run=run, at=at_minute(began + 1),
                               detail={"outcome": outcome, "run_minutes": minutes, "reason": None}))
        if queue_minutes is not None:
            wait = f"{run}-wait"
            events.append(envelope(kind="step.started", step="wait", run=wait, at=at_minute(began),
                                   detail={"reason": "github_queue", "on": run}))
            events.append(envelope(kind="step.ended", step="wait", run=wait,
                                   at=at_minute(began + queue_minutes),
                                   detail={"outcome": "passed", "reason": None}))
    return events


def plant(store, events):
    with store.writer() as writer:
        for event in events:
            writer.write(event)


def triage(hub):
    return hub.issues_with_label(REPO, "needs-triage")


def test_a_median_past_its_bound_opens_one_ticket_with_the_numbers(world):
    hub, store = world
    plant(store, runs(0, 10, "own", "linux", 9.0))
    report = run_bound_check(store, hub, SETTINGS)
    assert len(report.opened) == 1 and report.commented == []
    [number] = triage(hub)
    assert number == report.opened[0]
    issue = hub.issues[(REPO, number)]
    assert set(issue.labels) == {"needs-triage"}
    assert "own" in issue.title and "linux" in issue.title
    assert "9.0 min" in issue.body and "6 min" in issue.body


def test_a_second_crossing_comments_on_the_open_ticket_and_opens_none(world):
    hub, store = world
    plant(store, runs(0, 10, "own", "linux", 9.0))
    [number] = run_bound_check(store, hub, SETTINGS).opened
    plant(store, runs(10, 10, "own", "linux", 9.5))
    report = run_bound_check(store, hub, SETTINGS)
    assert report.opened == [] and report.commented == [number]
    assert triage(hub) == [number]
    [note] = hub.comments(REPO, number)
    assert "9.5 min" in note.body and "6 min" in note.body


def test_a_check_with_no_newer_run_says_nothing_again(world):
    hub, store = world
    plant(store, runs(0, 10, "own", "linux", 9.0))
    [number] = run_bound_check(store, hub, SETTINGS).opened
    again = run_bound_check(store, hub, SETTINGS)
    assert again.opened == [] and again.commented == []
    assert hub.comments(REPO, number) == []


def test_a_median_under_the_bound_gives_nothing(world):
    hub, store = world
    plant(store, runs(0, 10, "own", "linux", 3.0))
    report = run_bound_check(store, hub, SETTINGS)
    assert report.opened == [] and report.commented == []
    assert triage(hub) == []


def test_only_the_last_ten_runs_count(world):
    hub, store = world
    plant(store, runs(0, 10, "own", "linux", 9.0))
    plant(store, runs(10, 10, "own", "linux", 3.0))
    assert run_bound_check(store, hub, SETTINGS).opened == []


def test_a_different_os_gets_its_own_ticket_and_its_own_bound(world):
    hub, store = world
    plant(store, runs(0, 10, "github", "windows", 7.0))
    assert run_bound_check(store, hub, SETTINGS).opened == []
    plant(store, runs(0, 10, "own", "linux", 9.0))
    [linux] = run_bound_check(store, hub, SETTINGS).opened
    plant(store, runs(10, 10, "github", "windows", 9.0))
    [windows] = run_bound_check(store, hub, SETTINGS).opened
    assert linux != windows
    assert "linux" in hub.issues[(REPO, linux)].title
    assert "windows" in hub.issues[(REPO, windows)].title
    assert len(triage(hub)) == 2


def test_a_github_queue_wait_past_its_bound_opens_a_queue_ticket(world):
    hub, store = world
    plant(store, runs(0, 10, "github", "linux", 3.0, queue_minutes=7.0))
    [number] = run_bound_check(store, hub, SETTINGS).opened
    issue = hub.issues[(REPO, number)]
    assert "queue" in issue.title.lower()
    assert "7.0 min" in issue.body and "5 min" in issue.body


def test_the_bounds_come_from_settings_when_given(world):
    hub, store = world
    plant(store, runs(0, 10, "own", "linux", 9.0))
    settings = SETTINGS | {"place_bounds": {"run_minutes": {"linux": 20}, "queue_minutes": 5}}
    assert run_bound_check(store, hub, settings).opened == []
    assert triage(hub) == []
