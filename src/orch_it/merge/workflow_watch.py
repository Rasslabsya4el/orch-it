"""The workflow watch's decision (#411 rule 3, unit 2): what a non-green workflow run opens, comments on or
closes. `decide` reads the latest ended run of each workflow and the open watch issues, and returns actions;
it does no I/O. A run is non-green when it ended `failure`, `cancelled` or `timed_out`, or when it started
no job at all whatever GitHub labels its outcome. The `tests` workflow is skipped: `check_main` owns it, so
the caller passes its name in `skip`.

A watch ticket is written in #398's shape and checked by `tickets.violations` before it is returned, as
`red_main.build` does: a broken body is a bug of this module, so it raises.
"""

from __future__ import annotations

from dataclasses import dataclass

from orch_it.tickets import parse, violations
from orch_it.tickets.reader import TRIAGE_LABEL

MARK = "<!-- orch-it workflow-watch -->"
LABELS = (TRIAGE_LABEL,)
NON_GREEN = frozenset({"failure", "cancelled", "timed_out"})
HOW = {"failure": "failure", "cancelled": "cancelled", "timed_out": "timed out"}
NO_JOB = "no job started"


class WatchShapeError(AssertionError):
    """A watch ticket this module wrote breaks a shape rule: a bug of this module, never of the input."""


@dataclass(frozen=True)
class WorkflowRun:
    workflow: str
    run_id: int
    url: str
    ended_at: str
    outcome: str  # success, failure, cancelled, timed_out, skipped or other
    jobs_started: int


@dataclass(frozen=True)
class WatchIssue:
    number: int
    workflow: str
    last_run_id: int | None = None  # the run the issue last named; a repeat of it is no action


@dataclass(frozen=True)
class Open:
    workflow: str
    run: WorkflowRun
    title: str
    body: str


@dataclass(frozen=True)
class Comment:
    issue_number: int
    run: WorkflowRun
    body: str


@dataclass(frozen=True)
class Close:
    issue_number: int
    run: WorkflowRun
    body: str


Action = Open | Comment | Close


def non_green(run: WorkflowRun) -> bool:
    return run.jobs_started == 0 or run.outcome in NON_GREEN


def how_ended(run: WorkflowRun) -> str:
    """How a non-green run ended, in the words the watch ticket names."""

    return NO_JOB if run.jobs_started == 0 else HOW[run.outcome]


def _checked(title: str, body: str) -> tuple[str, str]:
    broken = violations(parse(body, title=title, labels=LABELS))
    if broken:
        raise WatchShapeError(", ".join(v.message for v in broken))
    return title, body


def build(run: WorkflowRun) -> tuple[str, str]:
    """(title, body) of the watch ticket for the non-green latest `run` of its workflow."""

    name, how = run.workflow, how_ended(run)
    title = f"Workflow {name} did not end green"
    body = f"""{MARK}
## For the human
- **What:** workflow `{name}` не завершился зелёным: последний прогон {run.run_id} закончился без зелёного результата.
- **Goal:** последний прогон `{name}` снова зелёный, и тикет закрывается сам на первом таком прогоне.
- **Plan:** разобрать, почему прогон не зелёный, и починить причину; что именно чинить, решает этот тикет.
- **Result:** последний прогон `{name}` зелёный.
- **Undo:** two-way door (один откат); touches только запуск `{name}`.

## For the agent
Facts: the workflow `{name}` ended its latest run {run.run_id} at {run.url} as {how}; it ended {run.ended_at}.
Rule: make the latest run of `{name}` green again by a fix or a revert; the watch closes on its first green run.

| row | check | pass |
|---|---|---|
| 1 | the next run of `{name}` | ends with success and at least one job started |

Outside case: a run that GitHub labels success but that started no job (row 1).
Same object: the watch comments on this issue at each further non-green run of `{name}` and closes it on a green one;
the `tests` workflow is not watched here, its red is the red-on-main ticket.
Weight: 2
"""
    return _checked(title, body)


def _comment(run: WorkflowRun) -> str:
    return (f"Workflow `{run.workflow}` did not end green again: run {run.run_id} ended as {how_ended(run)} "
            f"at {run.url}, ended {run.ended_at}.")


def _close(run: WorkflowRun) -> str:
    return (f"Workflow `{run.workflow}` ends green: run {run.run_id} at {run.url}, ended {run.ended_at}. "
            "Closing this watch.")


def decide(latest: list[WorkflowRun], open_issues: list[WatchIssue], *, skip: frozenset[str]) -> list[Action]:
    """The actions for the latest ended run of each workflow: a non-green one opens its watch or comments on
    the lowest-numbered open one (no comment for a run it already names); a green one closes every open watch
    of its workflow; a workflow in `skip` gets nothing."""

    actions: list[Action] = []
    for run in latest:
        if run.workflow in skip:
            continue
        watches = sorted((issue for issue in open_issues if issue.workflow == run.workflow),
                         key=lambda issue: issue.number)
        if not non_green(run):
            actions += [Close(issue.number, run, _close(run)) for issue in watches]
        elif not watches:
            title, body = build(run)
            actions.append(Open(run.workflow, run, title, body))
        elif watches[0].last_run_id != run.run_id:
            actions.append(Comment(watches[0].number, run, _comment(run)))
    return actions
