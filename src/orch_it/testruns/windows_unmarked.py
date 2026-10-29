"""The thin Windows job's two checks (#414 unit 4, row 5).

`run_unmarked_check`: the Windows nightly runs the whole suite, the change run only the tests marked `os_touching`.
A test that failed in the newest ended nightly run's Windows report, carries no marker there and did not fail in
any other job's report passes on Linux and is a marker the repo lacks. Each such test gets exactly one open
`needs-triage` ticket naming it (its fix is adding the marker); a later night that fails it again comments on that
ticket and opens none; a night looked at twice comments once. A marked test, and a test that also fails on Linux,
get no ticket. The nightly is a `schedule` or `workflow_dispatch` run (`github.NIGHTLY_EVENTS`).

`post_windows_time`: the pytest time of each of the last `windows_change_runs` ended per-change runs of the thin job
the store holds (a run whose `selection` in the store is not `nightly`), against `windows_change_minutes`. The three
numbers are always posted: as `WindowsTime.line` (a result line the closing quotes) and, when a time ticket is open,
as a comment on it once per newest run. "In a row over the target" is each of the three over it, not their median;
only then one `needs-triage` ticket opens (or the open one gets the slowest tests in its comment). A per-change run
takes only the marked tests, so its slowest tests are the slowest marked tests. The store keeps no mark per test, and
a per-change run from before the thin set (the whole suite) is not told apart from one after it.

`post_windows_nightly_time`: the pytest time of the newest ended nightly run of the thin job, posted the same way.

The job's check name, its OS label and the numbers are settings (`config.windows_thin`), never literals here; the
default branch is the repo's own (`hub.default_branch`).
"""

from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass, field

from orch_it.events.store import Store
from orch_it.merge import main_check, red_main
from orch_it.merge.follow import ROLE
from orch_it.merge.hub import PENDING, PullHub
from orch_it.testruns.bound_check import open_ticket_with, told
from orch_it.testruns.config import WindowsThin, windows_thin
from orch_it.testruns.github import NIGHTLY_EVENTS
from orch_it.tickets.hub import LAYER_MARK
from orch_it.tickets.model import CODE_WRITTEN_MARK
from orch_it.tickets.reader import TRIAGE_LABEL

RED = ("failed", "error")
MEASURED = ("passed", "failed")
TIME_MARK = "<!-- orch-it windows-time -->"


@dataclass(frozen=True)
class UnmarkedReport:
    opened: list[int] = field(default_factory=list)  # the tickets opened by this check
    commented: list[int] = field(default_factory=list)  # the open tickets a later night commented on


@dataclass(frozen=True)
class WindowsTime:
    runs: list[str]  # the per-change runs of the thin job the numbers are of, newest first
    minutes: list[float]  # the pytest time of each run, same order
    median: float | None  # minutes; None while fewer than the asked number of runs ended
    over: bool  # each of the runs is over the target
    line: str  # the result line with the numbers; empty while there is no verdict
    slowest: list[tuple[str, float]] = field(default_factory=list)  # (test id, seconds), slowest first; only when over
    ticket: int | None = None  # the ticket opened or commented on
    opened: bool = False  # the ticket is new


@dataclass(frozen=True)
class NightlyTime:
    run: str | None  # the newest ended nightly run of the thin job; None when the store holds none
    minutes: float | None
    line: str  # the result line; empty with no run
    ticket: int | None = None  # the open time ticket the line was commented on


def run_unmarked_check(store: Store, hub: PullHub, settings: dict) -> UnmarkedReport:
    """Open or comment on a `needs-triage` ticket for each unmarked test that failed only on the newest night's thin job."""

    repo = settings["repo"]
    thin = windows_thin(settings, repo)
    nights = [run for run in hub.branch_runs(repo, hub.default_branch(repo))
              if run.event in NIGHTLY_EVENTS and run.jobs and PENDING not in run.jobs.values()]
    results = hub.results(repo, nights[0].run_id) if nights else None
    if not results:
        return UnmarkedReport()
    night = nights[0].run_id
    marked = {red_main.normal(r.test_id) for r in results if r.job == thin.check and r.marked}
    elsewhere = {red_main.normal(r.test_id) for r in results if r.job != thin.check and r.outcome in RED}
    on_thin = {red_main.normal(r.test_id) for r in results if r.job == thin.check and r.outcome in RED}
    opened, commented = [], []
    for test_id in sorted(on_thin - marked - elsewhere - {""}):
        mark = f"<!-- orch-it windows-unmarked test={hashlib.sha256(test_id.encode()).hexdigest()[:16]} -->"
        note = f"<!-- orch-it windows-unmarked-note run={night} -->"
        ticket = open_ticket_with(hub, repo, mark)
        if ticket is None:
            title, body = _ticket(test_id, thin, mark, note)
            opened.append(main_check.open_triage(hub, store, repo, ROLE, title, body, (TRIAGE_LABEL,)))
        elif not told(hub, repo, ticket, note):
            hub.comment(repo, ticket.number, f"{LAYER_MARK}{note}\nFailed again on the {thin.os} nightly `{night}`: "
                                             f"{_facts(test_id, thin)}")
            commented.append(ticket.number)
    return UnmarkedReport(opened=opened, commented=commented)


def post_windows_time(store: Store, hub: PullHub, settings: dict) -> WindowsTime:
    """Post the pytest time of the last per-change runs against the target; each over it, open or comment on the ticket."""

    repo = settings["repo"]
    thin = windows_thin(settings, repo)
    timed = _ended_runs(store, repo, thin.os, nightly=False)[:thin.runs]
    runs, minutes = [run for run, _ in timed], [taken for _, taken in timed]
    if thin.runs < 1 or len(timed) < thin.runs:
        return WindowsTime(runs, minutes, None, False, "")
    median = statistics.median(minutes)
    over = all(taken > thin.change_minutes for taken in minutes)
    line = _time_line(thin, runs, minutes, over)
    slowest = _slowest(store, repo, runs, thin.slowest) if over else []
    note = f"<!-- orch-it windows-time-note run={runs[0]} -->"
    text = line + (f" The slowest tests: {_named(slowest)}." if over else "")
    ticket = open_ticket_with(hub, repo, TIME_MARK)
    if ticket is None and not over:
        return WindowsTime(runs, minutes, median, False, line)
    if ticket is None:
        title = f"{thin.os} per-change pytest time over {thin.change_minutes:g} min in {thin.runs} runs in a row"
        body = f"{TIME_MARK}\n{note}\n{CODE_WRITTEN_MARK}\n## Facts\n{text}\n"
        number = main_check.open_triage(hub, store, repo, ROLE, title, body, (TRIAGE_LABEL,))
        return WindowsTime(runs, minutes, median, True, line, slowest, number, True)
    if not told(hub, repo, ticket, note):
        hub.comment(repo, ticket.number, f"{LAYER_MARK}{note}\n{text}")
    return WindowsTime(runs, minutes, median, over, line, slowest, ticket.number)


def post_windows_nightly_time(store: Store, hub: PullHub, settings: dict) -> NightlyTime:
    """Post the pytest time of the newest ended nightly run: a comment on the open time ticket, once per run."""

    repo = settings["repo"]
    thin = windows_thin(settings, repo)
    nights = _ended_runs(store, repo, thin.os, nightly=True)
    if not nights:
        return NightlyTime(None, None, "")
    run, taken = nights[0]
    line = f"The {thin.os} nightly run {run} took {taken:.1f} min of pytest (the whole suite)."
    ticket = open_ticket_with(hub, repo, TIME_MARK)
    if ticket is None:
        return NightlyTime(run, taken, line)
    note = f"<!-- orch-it windows-nightly-note run={run} -->"
    if not told(hub, repo, ticket, note):
        hub.comment(repo, ticket.number, f"{LAYER_MARK}{note}\n{line}")
    return NightlyTime(run, taken, line, ticket.number)


def _ended_runs(store: Store, repo: str, os_name: str, *, nightly: bool) -> list[tuple[str, float]]:
    """(run, pytest minutes) of the repo's ended passed or failed test runs of `os_name` at GitHub, newest end first:
    the nightly ones, or the per-change ones (every selection but `nightly`)."""

    chosen = {run for (run,) in store.query(
        "SELECT run FROM events WHERE kind = 'step.started' AND step = 'test_run' AND repo = :repo"
        " AND place = 'github' AND run_os = :os"
        f" AND COALESCE(selection, '') {'=' if nightly else '!='} 'nightly' AND run IS NOT NULL",
        {"repo": repo, "os": os_name}).rows}
    ended = {}
    for run, outcome, minutes, at in store.query(
            "SELECT run, outcome, run_minutes, at FROM events WHERE kind = 'step.ended' AND step = 'test_run'"
            " AND repo = :repo AND run IS NOT NULL ORDER BY at, id", {"repo": repo}).rows:
        if run in chosen and run not in ended and outcome in MEASURED and isinstance(minutes, (int, float)):
            ended[run] = (at, float(minutes))
    newest = sorted(ended.items(), key=lambda item: (item[1][0], item[0]), reverse=True)
    return [(run, minutes) for run, (_, minutes) in newest]


def _slowest(store: Store, repo: str, runs: list[str], count: int) -> list[tuple[str, float]]:
    params = {"repo": repo} | {f"r{n}": run for n, run in enumerate(runs)}
    marks = ", ".join(f":r{n}" for n in range(len(runs)))
    longest: dict[str, float] = {}
    for test_id, ms in store.query(
            "SELECT test_id, ms FROM events WHERE kind = 'test.result' AND repo = :repo"
            f" AND run IN ({marks}) AND ms IS NOT NULL", params).rows:
        longest[test_id] = max(longest.get(test_id, 0.0), ms / 1000)
    return sorted(longest.items(), key=lambda item: (-item[1], item[0]))[:count]


def _named(slowest: list[tuple[str, float]]) -> str:
    return "; ".join(f"`{test_id}` {seconds:.1f} s" for test_id, seconds in slowest) or "no per-test time is stored"


def _time_line(thin: WindowsThin, runs: list[str], minutes: list[float], over: bool) -> str:
    verdict = "each over" if over else "not each over"
    return (f"The {thin.os} per-change pytest time of the last {len(runs)} runs ({', '.join(runs)}), newest first: "
            f"{', '.join(f'{taken:.1f}' for taken in minutes)} min, against its target of {thin.change_minutes:g} min: "
            f"{verdict} it.")


def _facts(test_id: str, thin: WindowsThin) -> str:
    return (f"`{test_id}` failed in the `{thin.check}` job, passed or did not fail in the other jobs and has no "
            f"`os_touching` marker. Fix: add the marker to the test, so the {thin.os} change run takes it.")


def _ticket(test_id: str, thin: WindowsThin, mark: str, note: str) -> tuple[str, str]:
    title = f"Add the os_touching marker: {test_id} fails only on {thin.os}"
    body = (f"{mark}\n{note}\n{CODE_WRITTEN_MARK}\n## Facts\n{_facts(test_id, thin)} The {thin.os} nightly check opened "
            "this ticket; a later night that fails the test again comments here instead of opening another.\n")
    return title, body
