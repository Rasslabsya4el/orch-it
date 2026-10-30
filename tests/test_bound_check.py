"""The bound check (#414 item 8; acceptance row 9): a place's median run time, or a GitHub place's median queue
wait, over its last ten runs, past its bound, opens one `needs-triage` ticket with the numbers; a later crossing
of the same repo, place, OS and kind comments on that open ticket and opens no new one. The bounds are the
defaults of `bound_defaults.json`, read through `default_bounds()`; the planted runs sit under, on or over them
by fixed steps. A run of N minutes is a passed run whose `run_minutes` is N."""

import copy
from datetime import UTC, datetime, timedelta

import pytest
from fake_github import REPO, FakeGitHub
from required_names import LINUX_OS, WINDOWS_OS

from orch_it.events import schema
from orch_it.events.store import Store
from orch_it.testruns.bound_check import default_bounds, run_bound_check
from orch_it.tickets.model import CODE_WRITTEN_MARK

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
SETTINGS = {"repo": REPO}
DEFAULTS = default_bounds()
LINUX = DEFAULTS["run_minutes"][LINUX_OS]
WINDOWS = DEFAULTS["run_minutes"][WINDOWS_OS]
QUEUE = DEFAULTS["queue_minutes"]


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
    plant(store, runs(0, 10, "own", LINUX_OS, LINUX + 3))
    report = run_bound_check(store, hub, SETTINGS)
    assert len(report.opened) == 1 and report.commented == []
    [number] = triage(hub)
    assert number == report.opened[0]
    issue = hub.issues[(REPO, number)]
    assert set(issue.labels) == {"needs-triage"}
    assert "own" in issue.title and LINUX_OS in issue.title
    assert f"{LINUX + 3:.1f} min" in issue.body and f"{LINUX:g} min" in issue.body


def test_a_second_crossing_comments_on_the_open_ticket_and_opens_none(world):
    hub, store = world
    plant(store, runs(0, 10, "own", LINUX_OS, LINUX + 3))
    [number] = run_bound_check(store, hub, SETTINGS).opened
    plant(store, runs(10, 10, "own", LINUX_OS, LINUX + 3.5))
    report = run_bound_check(store, hub, SETTINGS)
    assert report.opened == [] and report.commented == [number]
    assert triage(hub) == [number]
    [note] = hub.comments(REPO, number)
    assert f"{LINUX + 3.5:.1f} min" in note.body and f"{LINUX:g} min" in note.body


def test_a_check_with_no_newer_run_says_nothing_again(world):
    hub, store = world
    plant(store, runs(0, 10, "own", LINUX_OS, LINUX + 3))
    [number] = run_bound_check(store, hub, SETTINGS).opened
    again = run_bound_check(store, hub, SETTINGS)
    assert again.opened == [] and again.commented == []
    assert hub.comments(REPO, number) == []


def test_a_median_under_the_bound_gives_nothing(world):
    hub, store = world
    plant(store, runs(0, 10, "own", LINUX_OS, LINUX / 2))
    report = run_bound_check(store, hub, SETTINGS)
    assert report.opened == [] and report.commented == []
    assert triage(hub) == []


def test_only_the_last_ten_runs_count(world):
    hub, store = world
    plant(store, runs(0, 10, "own", LINUX_OS, LINUX + 3))
    plant(store, runs(10, 10, "own", LINUX_OS, LINUX / 2))
    assert run_bound_check(store, hub, SETTINGS).opened == []


def test_a_different_os_gets_its_own_ticket_and_its_own_bound(world):
    hub, store = world
    plant(store, runs(0, 10, "github", WINDOWS_OS, WINDOWS - 1))
    assert run_bound_check(store, hub, SETTINGS).opened == []
    plant(store, runs(0, 10, "own", LINUX_OS, LINUX + 3))
    [linux] = run_bound_check(store, hub, SETTINGS).opened
    plant(store, runs(10, 10, "github", WINDOWS_OS, WINDOWS + 1))
    [windows] = run_bound_check(store, hub, SETTINGS).opened
    assert linux != windows
    assert LINUX_OS in hub.issues[(REPO, linux)].title
    assert WINDOWS_OS in hub.issues[(REPO, windows)].title
    assert len(triage(hub)) == 2


def test_a_github_queue_wait_past_its_bound_opens_a_queue_ticket(world):
    hub, store = world
    plant(store, runs(0, 10, "github", LINUX_OS, LINUX / 2, queue_minutes=QUEUE + 2))
    [number] = run_bound_check(store, hub, SETTINGS).opened
    issue = hub.issues[(REPO, number)]
    assert "queue" in issue.title.lower()
    assert f"{QUEUE + 2:.1f} min" in issue.body and f"{QUEUE:g} min" in issue.body


def test_the_bounds_come_from_settings_when_given(world):
    hub, store = world
    plant(store, runs(0, 10, "own", LINUX_OS, LINUX + 3))
    settings = SETTINGS | {"place_bounds": {"run_minutes": {LINUX_OS: 20}, "queue_minutes": QUEUE}}
    assert run_bound_check(store, hub, settings).opened == []
    assert triage(hub) == []


@pytest.mark.parametrize("label", ["ready-for-agent", "parked", "needs-info"])
def test_a_relabelled_open_ticket_is_still_the_one_a_later_crossing_comments_on(world, label):
    hub, store = world
    plant(store, runs(0, 10, "own", LINUX_OS, LINUX + 3))
    [number] = run_bound_check(store, hub, SETTINGS).opened
    hub.write(REPO, number, hub.issues[(REPO, number)].body, [label])
    plant(store, runs(10, 10, "own", LINUX_OS, LINUX + 3.5))
    report = run_bound_check(store, hub, SETTINGS)
    assert report.opened == [] and report.commented == [number]
    assert [n for (r, n), issue in hub.issues.items() if issue.open] == [number]
    [note] = hub.comments(REPO, number)
    assert f"{LINUX + 3.5:.1f} min" in note.body


def test_a_partial_run_override_keeps_the_other_os_at_its_default(world):
    hub, store = world
    plant(store, runs(0, 10, "own", LINUX_OS, LINUX + 3))
    plant(store, runs(0, 10, "own", WINDOWS_OS, WINDOWS + 1))
    settings = SETTINGS | {"place_bounds": {"run_minutes": {LINUX_OS: 20}}}
    [windows] = run_bound_check(store, hub, settings).opened
    assert WINDOWS_OS in hub.issues[(REPO, windows)].title
    assert f"{WINDOWS:g} min" in hub.issues[(REPO, windows)].body
    assert triage(hub) == [windows]


def test_a_median_lifted_across_the_bound_by_slow_runs_opens_one_ticket_then_comments(world):
    hub, store = world
    fast, slow = LINUX - 3, LINUX + 3
    plant(store, runs(0, 10, "own", LINUX_OS, fast))
    assert run_bound_check(store, hub, SETTINGS).opened == []
    plant(store, runs(10, 5, "own", LINUX_OS, slow))
    assert run_bound_check(store, hub, SETTINGS).opened == []  # 5 slow and 5 fast: the median is the bound itself
    plant(store, runs(15, 1, "own", LINUX_OS, slow))
    [number] = run_bound_check(store, hub, SETTINGS).opened
    assert f"{slow:.1f} min" in hub.issues[(REPO, number)].body and f"{LINUX:g} min" in hub.issues[(REPO, number)].body
    plant(store, runs(16, 1, "own", LINUX_OS, slow))
    report = run_bound_check(store, hub, SETTINGS)
    assert report.opened == [] and report.commented == [number]
    assert triage(hub) == [number]


# The headroom rule (#468): per (job, kind) the newest ten ended GitHub job runs, a run's share of the bound it ran
# under (a cut run counts as the whole bound); one ticket when the largest share is past 80%.

HEADROOM_BOUND = 16
JOB_GAP_MINUTES = 30  # one job run starts every 30 minutes, so runs never overlap
# 24 windows nightly wall times; the cut run (16.3 min, past a 16 min bound) is the 23rd, inside the newest ten
REAL_WALLS = [11.0, 12.5, 10.0, 9.5, 12.0, 11.5] * 3 + [10.5, 11.0, 12.0, 9.0, 16.3, 10.0]


@pytest.fixture
def job_world(tmp_path, monkeypatch):
    """A hub and a store whose closed sets hold the `foreign_run` step of the event contract (added when the
    closed-set file lacks it, so the test holds before and after the importer unit lands)."""

    sets = copy.deepcopy(schema._sets())
    sets["steps"].setdefault("foreign_run", copy.deepcopy(sets["steps"]["test_run"]))
    monkeypatch.setattr(schema, "_sets", lambda: sets)
    return FakeGitHub(NOW), Store.open(tmp_path / "events")


def job_runs(job, walls, *, start=0, bound=HEADROOM_BOUND, selection="nightly", only_started=(), lost=()):
    """One GitHub job run per wall time in minutes, run `start + i` starting `JOB_GAP_MINUTES` after the one before.
    A wall time at or past the bound is a run cut at it (`no_result`, reason `cut`); `only_started` lists the
    positions that never end, `lost` the positions that end `lost`."""

    events = []
    step = "foreign_run" if job.startswith("foreign") else "test_run"
    run_os = WINDOWS_OS if "windows" in job else LINUX_OS
    for i, wall in enumerate(walls):
        number = start + i
        run = f"{job}-{selection}-{number}"
        began = JOB_GAP_MINUTES * number
        events.append(envelope(kind="step.started", step=step, run=run, at=at_minute(began), detail={
            "place": "github", "run_os": run_os, "job": job, "job_bound_minutes": bound, "selection": selection,
            "event": "schedule" if selection == "nightly" else "pull_request"}))
        if i in only_started:
            continue
        detail = {"outcome": "passed", "reason": None}
        if i in lost:
            detail = {"outcome": "lost", "reason": None}
        elif wall >= bound:
            detail = {"outcome": "no_result", "reason": "cut"}
        events.append(envelope(kind="step.ended", step=step, run=run, at=at_minute(began + wall), detail=detail))
    return events


def test_a_longest_run_past_80_percent_opens_one_ticket_then_says_nothing_then_comments(job_world):
    hub, store = job_world
    plant(store, job_runs("windows", [8.0] * 9 + [13.6]))  # the longest is 85% of a 16 min bound
    report = run_bound_check(store, hub, SETTINGS)
    [number] = report.headroom_opened
    assert report.opened == [] and report.commented == [] and report.headroom_commented == []
    assert triage(hub) == [number]
    issue = hub.issues[(REPO, number)]
    assert set(issue.labels) == {"needs-triage"}
    assert "<!-- orch-it bound job=windows kind=nightly -->" in issue.body
    assert "## Facts" in issue.body and CODE_WRITTEN_MARK in issue.body
    assert "`windows`" in issue.body and "nightly" in issue.body
    assert "13.6 min" in issue.body and "16 min" in issue.body  # the longest run and its bound
    assert "8.0 min" in issue.body  # the median
    for n in range(10):
        assert f"windows-nightly-{n}" in issue.body
    again = run_bound_check(store, hub, SETTINGS)
    assert again.headroom_opened == [] and again.headroom_commented == []
    assert hub.comments(REPO, number) == []
    plant(store, job_runs("windows", [14.0], start=10))
    later = run_bound_check(store, hub, SETTINGS)
    assert later.headroom_opened == [] and later.headroom_commented == [number]
    assert triage(hub) == [number]
    [note] = hub.comments(REPO, number)
    assert "14.0 min" in note.body and "windows-nightly-10" in note.body


def test_a_cut_run_among_short_ones_opens_a_ticket_naming_the_cut_run(job_world):
    hub, store = job_world
    plant(store, job_runs("windows", [8.0] * 9 + [HEADROOM_BOUND]))  # the other nine are 50% of the bound
    [number] = run_bound_check(store, hub, SETTINGS).headroom_opened
    assert "windows-nightly-9" in hub.issues[(REPO, number)].body
    assert triage(hub) == [number]


def test_a_longest_run_at_exactly_80_percent_gives_nothing(job_world):
    hub, store = job_world
    plant(store, job_runs("windows", [8.0] * 9 + [12.8]))
    report = run_bound_check(store, hub, SETTINGS)
    assert report.headroom_opened == [] and report.headroom_commented == []
    assert triage(hub) == []


def test_the_real_windows_case_a_cut_in_the_newest_ten_of_24_runs(job_world):
    hub, store = job_world
    assert len(REAL_WALLS) == 24
    plant(store, job_runs("windows", REAL_WALLS))
    [number] = run_bound_check(store, hub, SETTINGS).headroom_opened
    assert "windows-nightly-22" in hub.issues[(REPO, number)].body
    assert triage(hub) == [number]


def test_the_same_24_runs_under_a_22_minute_bound_give_nothing(job_world):
    hub, store = job_world
    plant(store, job_runs("windows", REAL_WALLS, bound=22))  # 16.3 min is 74% of 22 and no run is cut
    report = run_bound_check(store, hub, SETTINGS)
    assert report.headroom_opened == [] and triage(hub) == []


def test_two_jobs_over_the_line_get_one_ticket_each(job_world):
    hub, store = job_world
    plant(store, job_runs("windows", [8.0] * 9 + [15.0]))
    plant(store, job_runs("foreign-windows", [8.0] * 9 + [15.0]))
    plant(store, job_runs("linux", [3.0] * 10))
    opened = run_bound_check(store, hub, SETTINGS).headroom_opened
    assert len(opened) == 2 and sorted(triage(hub)) == sorted(opened)
    bodies = [hub.issues[(REPO, n)].body for n in opened]
    assert sum("<!-- orch-it bound job=windows kind=nightly -->" in b for b in bodies) == 1
    assert sum("<!-- orch-it bound job=foreign-windows kind=nightly -->" in b for b in bodies) == 1


def test_a_run_with_no_end_or_a_lost_outcome_is_left_out(job_world):
    hub, store = job_world
    plant(store, job_runs("windows", [8.0, 8.0, 8.0, 20.0, 20.0], only_started=(3,), lost=(4,)))
    report = run_bound_check(store, hub, SETTINGS)
    assert report.headroom_opened == [] and triage(hub) == []


def test_a_nightly_and_a_per_change_run_of_one_job_are_two_kinds(job_world):
    hub, store = job_world
    plant(store, job_runs("linux", [3.0] * 10, selection="nightly"))
    plant(store, job_runs("linux", [3.0] * 9 + [15.0], selection="full"))
    [number] = run_bound_check(store, hub, SETTINGS).headroom_opened
    assert "<!-- orch-it bound job=linux kind=per_change -->" in hub.issues[(REPO, number)].body


def test_fewer_than_ten_runs_use_what_exists(job_world):
    hub, store = job_world
    plant(store, job_runs("windows", [14.0]))
    assert len(run_bound_check(store, hub, SETTINGS).headroom_opened) == 1


def test_the_two_rules_never_comment_on_each_others_tickets(job_world):
    hub, store = job_world
    plant(store, runs(0, 10, "github", WINDOWS_OS, WINDOWS + 1))  # a median past its bound
    plant(store, job_runs("windows", [8.0] * 9 + [15.0], start=100))
    report = run_bound_check(store, hub, SETTINGS)
    assert len(report.opened) == 1 and len(report.headroom_opened) == 1
    assert report.opened != report.headroom_opened
    again = run_bound_check(store, hub, SETTINGS)
    assert again.opened == again.commented == again.headroom_opened == again.headroom_commented == []
