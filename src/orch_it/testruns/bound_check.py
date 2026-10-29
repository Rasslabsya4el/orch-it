"""The bound check (#414 item 8): per test place and OS, the median run time (`run`) of the last ten ended
test runs and, at a GitHub place, the median queue wait (`queue`) of its last ten runs. When a median is past
its bound, one `needs-triage` ticket opens with the numbers; a later crossing of the same repo, place, OS and
kind comments on that open ticket and opens no new one.

A run counts for the run median only when it passed or failed (a cut or stopped run measures nothing, as in
`test-places`). A crossing comments only when a run newer than the one the ticket last heard of is in the
window; a check with no newer run says nothing again.

Bounds are settings, read with `dict.get` from the `place_bounds` key: `run_minutes` maps an OS to its bound,
`queue_minutes` is one bound for every GitHub place. A key the settings do not give takes DEFAULT_BOUNDS; an OS
with no run bound is not checked. The tickets go through `merge.main_check`'s triage helpers, the same path as
the no-result ticket to the brain.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from orch_it.events.store import Store
from orch_it.merge import main_check
from orch_it.merge.follow import ROLE
from orch_it.merge.hub import PullHub
from orch_it.tickets.hub import LAYER_MARK
from orch_it.tickets.model import CODE_WRITTEN_MARK
from orch_it.tickets.reader import TRIAGE_LABEL

BOUNDS_KEY = "place_bounds"
DEFAULT_BOUNDS = {"run_minutes": {"linux": 6, "windows": 8}, "queue_minutes": 5}
"""Measured per-change durations: a linux run 6 minutes, a windows run 8 minutes, a GitHub queue wait 5 minutes."""

LAST_RUNS = 10
MEASURED = ("passed", "failed")
NOT_MEASURED = "n/a"
KIND_WORDS = {"run": "run time", "queue": "GitHub queue wait"}


@dataclass(frozen=True)
class BoundReport:
    opened: list[int] = field(default_factory=list)  # the bound tickets opened by this check
    commented: list[int] = field(default_factory=list)  # the open bound tickets a later crossing commented on


@dataclass(frozen=True)
class _Run:
    run: str
    at: str  # when the run ended
    minutes: float | None  # the run's minutes, a passed or failed run only
    queue: float | None  # the minutes of its GitHub queue wait, a GitHub run only


@dataclass(frozen=True)
class _Crossing:
    place: str
    os: str
    kind: str  # `run` or `queue`
    median: float
    bound: float
    newest: str  # the newest run of the window
    count: int  # the runs the median is over


def run_bound_check(store: Store, hub: PullHub, settings: dict) -> BoundReport:
    """Open or comment on a `needs-triage` ticket for each place and OS whose median is past its bound."""

    repo = settings["repo"]
    run_bounds, queue_bound = _bounds(settings)
    opened, commented = [], []
    for crossing in _crossings(_recent_runs(store, repo), run_bounds, queue_bound):
        mark, note = _key_mark(crossing), _note_mark(crossing)
        number = main_check.open_triage_with(hub, repo, mark)
        if number is None:
            title, body = _ticket(crossing, mark, note)
            opened.append(main_check.open_triage(hub, store, repo, ROLE, title, body, (TRIAGE_LABEL,)))
        elif not _told(hub, repo, number, note):
            hub.comment(repo, number, _comment(crossing, note))
            commented.append(number)
    return BoundReport(opened=opened, commented=commented)


def _bounds(settings: dict) -> tuple[dict, float]:
    given = settings.get(BOUNDS_KEY, DEFAULT_BOUNDS)
    run_bounds = given.get("run_minutes", DEFAULT_BOUNDS["run_minutes"])
    queue_bound = given.get("queue_minutes", DEFAULT_BOUNDS["queue_minutes"])
    return run_bounds, queue_bound


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _recent_runs(store: Store, repo: str) -> dict[tuple[str, str], list[_Run]]:
    """(place, OS) -> the repo's ended test runs there, newest first."""

    placed = {}  # run -> (place, os)
    for run, place, run_os in store.query(
            "SELECT run, place, run_os FROM events WHERE kind = 'step.started' AND step = 'test_run'"
            " AND repo = :repo AND run IS NOT NULL AND place IS NOT NULL ORDER BY at, id", {"repo": repo}).rows:
        placed.setdefault(run, (place, run_os))

    ended = {}  # run -> (at, outcome, run_minutes), the first end of the run
    for run, outcome, minutes, at in store.query(
            "SELECT run, outcome, run_minutes, at FROM events WHERE kind = 'step.ended' AND step = 'test_run'"
            " AND repo = :repo AND run IS NOT NULL ORDER BY at, id", {"repo": repo}).rows:
        if run in placed and run not in ended:
            ended[run] = (at, outcome, minutes)

    wait_ends = {}  # wait run -> the times its wait ended
    for run, at in store.query(
            "SELECT run, at FROM events WHERE kind = 'step.ended' AND step = 'wait'"
            " AND repo = :repo AND run IS NOT NULL ORDER BY at, id", {"repo": repo}).rows:
        wait_ends.setdefault(run, []).append(at)

    queue = {}  # github run -> its queue wait in minutes, the first one
    for run, on, at in store.query(
            "SELECT run, \"on\", at FROM events WHERE kind = 'step.started' AND step = 'wait'"
            " AND reason = 'github_queue' AND repo = :repo AND run IS NOT NULL ORDER BY at, id",
            {"repo": repo}).rows:
        if on not in placed or placed[on][0] != "github" or on in queue:
            continue
        end = next((e for e in wait_ends.get(run, []) if e >= at), None)
        if end is not None:
            queue[on] = (_moment(end) - _moment(at)).total_seconds() / 60

    groups = {}
    for run, (place, run_os) in placed.items():
        if run not in ended:
            continue
        at, outcome, minutes = ended[run]
        measured = float(minutes) if outcome in MEASURED and isinstance(minutes, (int, float)) else None
        groups.setdefault((place, run_os or NOT_MEASURED), []).append(_Run(run, at, measured, queue.get(run)))
    return {key: sorted(runs, key=lambda r: (r.at, r.run), reverse=True) for key, runs in groups.items()}


def _crossings(groups: dict, run_bounds: dict, queue_bound: float) -> list[_Crossing]:
    found = []
    for (place, run_os), runs in sorted(groups.items()):
        timed = [r for r in runs if r.minutes is not None][:LAST_RUNS]
        bound = run_bounds.get(run_os)
        if timed and bound is not None:
            median = statistics.median(r.minutes for r in timed)
            if median > bound:
                found.append(_Crossing(place, run_os, "run", median, bound, timed[0].run, len(timed)))
        if place == "github":
            waited = [r for r in runs if r.queue is not None][:LAST_RUNS]
            if waited:
                median = statistics.median(r.queue for r in waited)
                if median > queue_bound:
                    found.append(_Crossing(place, run_os, "queue", median, queue_bound, waited[0].run, len(waited)))
    return found


def _key_mark(c: _Crossing) -> str:
    return f"<!-- orch-it bound place={c.place} os={c.os} kind={c.kind} -->"


def _note_mark(c: _Crossing) -> str:
    return f"<!-- orch-it bound-note kind={c.kind} run={c.newest} -->"


def _ticket(c: _Crossing, mark: str, note: str) -> tuple[str, str]:
    title = f"Test {KIND_WORDS[c.kind]} over its bound at {c.place} on {c.os}"
    body = f"""{mark}
{note}
{CODE_WRITTEN_MARK}
## Facts
The median {KIND_WORDS[c.kind]} of the last {c.count} ended test runs at `{c.place}` on `{c.os}` is
{c.median:.1f} min, over its bound of {c.bound:g} min. The newest of those runs is `{c.newest}`. The bound check
opened this ticket; a later crossing by newer runs comments here instead of opening another.
"""
    return title, body


def _comment(c: _Crossing, note: str) -> str:
    return (f"{LAYER_MARK}{note}\n"
            f"The median {KIND_WORDS[c.kind]} of the last {c.count} ended test runs at `{c.place}` on `{c.os}` is "
            f"{c.median:.1f} min, over its bound of {c.bound:g} min again. The newest of those runs is `{c.newest}`.")


def _told(hub: PullHub, repo: str, number: int, note: str) -> bool:
    issue = hub.issue(repo, number)
    if issue is not None and note in issue.body:
        return True
    return any(note in said.body for said in hub.comments(repo, number))
