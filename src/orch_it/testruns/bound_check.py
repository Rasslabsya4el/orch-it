"""The bound check (#414 item 8): per test place and OS, the median run time (`run`) of the last ten ended
test runs and, at a GitHub place, the median queue wait (`queue`) of its last ten runs. When a median is past
its bound, one `needs-triage` ticket opens with the numbers; a later crossing of the same repo, place, OS and
kind comments on that open ticket and opens no new one.

A run counts for the run median only when it passed or failed (a cut or stopped run measures nothing, as in
`test-places`). A crossing comments only when a run newer than the one the ticket last heard of is in the
window; a check with no newer run says nothing again.

The headroom rule (#468) is the second rule: a job cut at its bound never reaches the median, so per (job, kind)
-- kind `nightly` when the run's start says selection `nightly`, else `per_change` -- the newest ten ended GitHub
job runs (steps `test_run` and `foreign_run`, passed, failed, or no_result with reason `cut`) are measured by
their wall time (end minus start) against the `job_bound_minutes` the run stored, a cut run counting as the whole
bound. When the largest share is past HEADROOM_SHARE, one ticket opens under its own mark, so the two rules never
comment on each other's tickets; a later run still past the line comments once.

Bounds are settings, read with `dict.get` from the `place_bounds` key: `run_minutes` maps an OS to its bound,
`queue_minutes` is one bound for every GitHub place. A key the settings do not give takes `default_bounds()`; an OS
with no run bound is not checked. The tickets go through `merge.main_check`'s triage helpers, the same path as
the no-result ticket to the brain.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from orch_it.events.store import Store
from orch_it.merge import main_check
from orch_it.merge.follow import ROLE
from orch_it.merge.hub import PullHub
from orch_it.tickets.hub import LAYER_MARK, Issue
from orch_it.tickets.model import CODE_WRITTEN_MARK
from orch_it.tickets.reader import TRIAGE_LABEL

BOUNDS_KEY = "place_bounds"
DEFAULTS_FILE = Path(__file__).with_name("bound_defaults.json")

LAST_RUNS = 10
MEASURED = ("passed", "failed")
NOT_MEASURED = "n/a"
HEADROOM_SHARE = 0.8  # the largest share of its bound a job run may take before the headroom ticket opens
CUT = "cut"
NIGHTLY = "nightly"
PER_CHANGE = "per_change"
KIND_WORDS = {"run": "run time", "queue": "GitHub queue wait"}


@dataclass(frozen=True)
class BoundReport:
    opened: list[int] = field(default_factory=list)  # the bound tickets opened by this check
    commented: list[int] = field(default_factory=list)  # the open bound tickets a later crossing commented on
    headroom_opened: list[int] = field(default_factory=list)  # the headroom tickets opened by this check
    headroom_commented: list[int] = field(default_factory=list)  # the open headroom tickets commented on


@dataclass(frozen=True)
class _Run:
    run: str
    at: str  # when the run ended
    minutes: float | None  # the run's minutes, a passed or failed run only
    queue: float | None  # the minutes of its GitHub queue wait, a GitHub run only


@dataclass(frozen=True)
class _JobRun:
    run: str
    at: str  # when the run ended
    minutes: float  # the job's wall time: end minus start
    bound: float  # the job's timeout bound in minutes, as the run stored it
    cut: bool  # the run ended at its bound

    @property
    def share(self) -> float:
        return 1.0 if self.cut else self.minutes / self.bound


@dataclass(frozen=True)
class _Headroom:
    job: str
    kind: str  # `nightly` or `per_change`
    runs: list[_JobRun]  # the newest ten ended runs, newest first
    worst: _JobRun  # the run with the largest share of its bound
    newest_crossing: _JobRun  # the newest run of the window past the line: the one a comment tells of


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
    """Open or comment on a `needs-triage` ticket for each place and OS whose median is past its bound, and for
    each job and kind whose largest share of its bound is past the headroom line."""

    repo = settings["repo"]
    run_bounds, queue_bound = _bounds(settings)
    opened, commented = [], []
    for crossing in _crossings(_recent_runs(store, repo), run_bounds, queue_bound):
        mark, note = _key_mark(crossing), _note_mark(crossing)
        title, body = _ticket(crossing, mark, note)
        _tell(store, hub, repo, mark, note, title, body, _comment(crossing, note), opened, commented)
    headroom_opened, headroom_commented = [], []
    for tight in _headrooms(_job_runs(store, repo)):
        mark, note = _headroom_key_mark(tight), _headroom_note_mark(tight)
        _tell(store, hub, repo, mark, note, _headroom_title(tight), _headroom_body(tight, mark, note),
              f"{LAYER_MARK}{note}\nCrossed again. {_headroom_facts(tight)}", headroom_opened, headroom_commented)
    return BoundReport(opened, commented, headroom_opened, headroom_commented)


def _tell(store, hub, repo, mark, note, title, body, comment, opened, commented) -> None:
    """Open the ticket when no open issue holds `mark`; else comment once per `note`."""

    ticket = open_ticket_with(hub, repo, mark)
    if ticket is None:
        opened.append(main_check.open_triage(hub, store, repo, ROLE, title, body, (TRIAGE_LABEL,)))
    elif not told(hub, repo, ticket, note):
        hub.comment(repo, ticket.number, comment)
        commented.append(ticket.number)


def default_bounds() -> dict:
    """The default bounds of `bound_defaults.json`: `run_minutes` (OS -> minutes) and `queue_minutes`."""

    return json.loads(DEFAULTS_FILE.read_text("utf-8"))


def _bounds(settings: dict) -> tuple[dict, float]:
    defaults = default_bounds()
    given = settings.get(BOUNDS_KEY, {})
    return {**defaults["run_minutes"], **given.get("run_minutes", {})}, given.get("queue_minutes", defaults["queue_minutes"])


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


def _job_runs(store: Store, repo: str) -> dict[tuple[str, str], list[_JobRun]]:
    """(job, kind) -> the repo's ended GitHub job runs, newest first. `job_bound_minutes` is read from the detail
    JSON: a start with no job or no bound, and a run that never ended or ended another way, is left out."""

    started = {}  # run -> (job, kind, at, bound), the first start of the run
    for run, job, selection, bound, at in store.query(
            "SELECT run, json_extract(detail, '$.job'), json_extract(detail, '$.selection'),"
            " json_extract(detail, '$.job_bound_minutes'), at FROM events WHERE kind = 'step.started'"
            " AND step IN ('test_run', 'foreign_run') AND repo = :repo AND run IS NOT NULL ORDER BY at, id",
            {"repo": repo}).rows:
        if isinstance(job, str) and isinstance(bound, (int, float)) and bound > 0 and run not in started:
            started[run] = (job, NIGHTLY if selection == NIGHTLY else PER_CHANGE, at, float(bound))

    groups, seen = {}, set()
    for run, outcome, reason, at in store.query(
            "SELECT run, outcome, reason, at FROM events WHERE kind = 'step.ended'"
            " AND step IN ('test_run', 'foreign_run') AND repo = :repo AND run IS NOT NULL ORDER BY at, id",
            {"repo": repo}).rows:
        if run not in started or run in seen:
            continue
        seen.add(run)
        cut = outcome == "no_result" and reason == CUT
        if outcome in MEASURED or cut:
            job, kind, began, bound = started[run]
            minutes = (_moment(at) - _moment(began)).total_seconds() / 60
            groups.setdefault((job, kind), []).append(_JobRun(run, at, minutes, bound, cut))
    return {key: sorted(runs, key=lambda r: (r.at, r.run), reverse=True) for key, runs in groups.items()}


def _headrooms(groups: dict) -> list[_Headroom]:
    found = []
    for (job, kind), runs in sorted(groups.items()):
        window = runs[:LAST_RUNS]
        worst = window[0]
        for candidate in window:
            if candidate.share > worst.share:
                worst = candidate
        if worst.share > HEADROOM_SHARE:
            found.append(_Headroom(job, kind, window, worst, next(r for r in window if r.share > HEADROOM_SHARE)))
    return found


def _headroom_key_mark(h: _Headroom) -> str:
    return f"<!-- orch-it bound job={h.job} kind={h.kind} -->"


def _headroom_note_mark(h: _Headroom) -> str:
    return f"<!-- orch-it bound-note job={h.job} kind={h.kind} run={h.newest_crossing.run} -->"


def _headroom_facts(h: _Headroom) -> str:
    longest = max(r.minutes for r in h.runs)
    median = statistics.median(r.minutes for r in h.runs)
    cut = " (cut at its bound)" if h.worst.cut else ""
    keys = ", ".join(f"`{r.run}`" for r in h.runs)
    return (f"The job `{h.job}` ({h.kind.replace('_', ' ')} runs) took {h.worst.share:.0%} of its bound in its worst of "
            f"the last {len(h.runs)} ended runs: `{h.worst.run}`, {h.worst.minutes:.1f} min of a bound of "
            f"{h.worst.bound:g} min{cut}. The longest of those runs is {longest:.1f} min and their median is "
            f"{median:.1f} min. The runs, newest first: {keys}.")


def _headroom_title(h: _Headroom) -> str:
    return f"Job {h.job} ({h.kind.replace('_', ' ')}) is over {HEADROOM_SHARE:.0%} of its time bound"


def _headroom_body(h: _Headroom, mark: str, note: str) -> str:
    return (f"{mark}\n{note}\n{CODE_WRITTEN_MARK}\n## Facts\n{_headroom_facts(h)} The bound check opened this "
            "ticket; a later run still over the line comments here instead of opening another.\n")


def _key_mark(c: _Crossing) -> str:
    return f"<!-- orch-it bound place={c.place} os={c.os} kind={c.kind} -->"


def _note_mark(c: _Crossing) -> str:
    return f"<!-- orch-it bound-note kind={c.kind} run={c.newest} -->"


def _facts(c: _Crossing) -> str:
    return (f"The median {KIND_WORDS[c.kind]} of the last {c.count} ended test runs at `{c.place}` on `{c.os}` is "
            f"{c.median:.1f} min, over its bound of {c.bound:g} min. The newest of those runs is `{c.newest}`.")


def _ticket(c: _Crossing, mark: str, note: str) -> tuple[str, str]:
    title = f"Test {KIND_WORDS[c.kind]} over its bound at {c.place} on {c.os}"
    body = (f"{mark}\n{note}\n{CODE_WRITTEN_MARK}\n## Facts\n{_facts(c)} The bound check opened this ticket; a later "
            "crossing by newer runs comments here instead of opening another.\n")
    return title, body


def _comment(c: _Crossing, note: str) -> str:
    return f"{LAYER_MARK}{note}\nCrossed again. {_facts(c)}"


def open_ticket_with(hub: PullHub, repo: str, mark: str) -> Issue | None:
    """The lowest-numbered open issue, whatever its labels, whose body holds `mark`; None when there is none."""

    found = sorted((issue for issue in hub.open_issues(repo) if mark in issue.body), key=lambda issue: issue.number)
    return found[0] if found else None


def told(hub: PullHub, repo: str, ticket: Issue, note: str) -> bool:
    if note in ticket.body:
        return True
    return any(note in said.body for said in hub.comments(repo, ticket.number))
