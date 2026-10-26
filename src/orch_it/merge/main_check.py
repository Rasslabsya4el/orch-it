"""Main is checked after every merge (#400 decision 4): `check_main` reads the runs of the `tests`
workflow on a branch (a push, so after every merge, and the nightly run) and keeps the red-on-main
tickets true to the newest run that ended:

- green on every required job: every open red-on-main ticket closes (`green`);
- red: the failing ids from the run's reports. An open red-on-main ticket none of whose tests
  failed closes (they pass now); the ids no open ticket lists get one new ticket, ready for an
  agent, `priority`, in #398's shape, naming the pull request that brought the run's commit; none
  is opened for ids already listed (`red`). A red run that names no test is no result;
- no result (a job cut, cancelled, skipped, or red with no report): the run runs once more
  (`rerun`); no result again on its second attempt opens one ticket to the brain for that run
  (`no_result`).

When the newest run has not ended, an older ended run may open a red ticket but closes none: the
newer run may be red. No skip list exists: red on main is its own ticket (MERGE-7). The workflow
`main-check` calls it when a `tests` run on the default branch ends, `reconcile` on its schedule.
Events: `main.red` with `ticket.opened` per new red ticket, `ticket.closed` per closed one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from orch_it.events.store import Store
from orch_it.merge import marks, record, red_main
from orch_it.merge.hub import FAILED, PASSED, PENDING, MainRun, PullHub
from orch_it.merge.settings import MergeSettings
from orch_it.tickets.model import PRIORITY_LABEL
from orch_it.tickets.reader import TRIAGE_LABEL

ROLE = "github"


@dataclass(frozen=True)
class MainReport:
    outcome: str  # green, red, rerun, no_result, waiting, no_run
    run_id: str | None = None
    sha: str | None = None
    opened: list[int] = field(default_factory=list)
    closed: list[int] = field(default_factory=list)


def check_main(hub: PullHub, store: Store, repo: str, branch: str, *, settings: MergeSettings, now: datetime,
               role: str = ROLE) -> MainReport:
    """Bring the red-on-main tickets in line with the newest ended `tests` run on `branch`."""

    runs = hub.branch_runs(repo, branch)
    ended = [run for run in runs if run.jobs and PENDING not in run.jobs.values()]
    if not ended:
        return MainReport("waiting" if runs else "no_run")
    run, newest = ended[0], ended[0] is runs[0]
    states = set(run.jobs.values())
    reds = _open_reds(hub, repo)
    if states == {PASSED}:
        if not newest:
            return MainReport("waiting", run.run_id, run.sha)
        closed = [_close(hub, store, repo, number, run, role) for number, _ in reds]
        return MainReport("green", run.run_id, run.sha, closed=closed)
    ids = hub.failing_ids(repo, run.run_id) if FAILED in states else None
    if ids:
        return _red(hub, store, repo, run, sorted(set(ids)), reds, newest, role)
    if run.attempt >= 2:
        return MainReport("no_result", run.run_id, run.sha, opened=_to_the_brain(hub, store, repo, run, role))
    if newest:
        hub.rerun(repo, run.run_id)
    return MainReport("rerun", run.run_id, run.sha)


def _open_reds(hub: PullHub, repo: str) -> list[tuple[int, frozenset[str]]]:
    found = []
    for number in hub.issues_with_label(repo, PRIORITY_LABEL):
        issue = hub.issue(repo, number)
        ids = red_main.ids_of(issue.body) if issue is not None and issue.open else None
        if ids:
            found.append((number, ids))
    return found


def _red(hub: PullHub, store: Store, repo: str, run: MainRun, ids: list[str],
         reds: list[tuple[int, frozenset[str]]], newest: bool, role: str) -> MainReport:
    failing = set(ids)
    closed = [_close(hub, store, repo, number, run, role) for number, listed in reds
              if newest and not listed & failing]
    listed = set().union(*(listed for number, listed in reds if number not in closed))
    new = sorted(failing - listed)
    if not new:
        return MainReport("red", run.run_id, run.sha, closed=closed)
    pulls = hub.pulls_of_commit(repo, run.sha)
    culprit = pulls[0] if pulls else None
    title, body = red_main.build(new, sha=run.sha, run_id=run.run_id, pr=culprit)
    number = hub.create_issue(repo, title, body, list(red_main.LABELS))
    # a second check racing this one may have opened the same ticket a moment before: the older one stays
    twins = [n for n, listed in _open_reds(hub, repo) if n != number and listed == red_main.ids_of(body)]
    if twins and min(twins) < number:
        hub.comment(repo, number, f"{marks.follow_mark('duplicate')}The same failing tests are #{min(twins)}'s.")
        hub.close_issue(repo, number, "not_planned")
        return MainReport("red", run.run_id, run.sha, closed=closed)
    record.write(store, repo, number, role, None, None, [
        ("main.red", {"sha": run.sha, "test_ids": new, "introduced_by_pr": culprit}),
        ("ticket.opened", {"title": title, "labels": list(red_main.LABELS), "parent": None, "weight": red_main.WEIGHT,
                           "priority": True, "write_ticket_called": False, "has_outside_case": True,
                           "acceptance_rows": 3})])
    return MainReport("red", run.run_id, run.sha, opened=[number], closed=closed)


def _close(hub: PullHub, store: Store, repo: str, number: int, run: MainRun, role: str) -> int:
    hub.comment(repo, number, f"{marks.follow_mark('closed')}Main is green on these tests in the `tests` run "
                              f"{run.run_id} at `{run.sha[:12]}`: closed.")
    hub.close_issue(repo, number, "completed")
    record.write(store, repo, number, role, None, None, [
        ("comment.written", {"comment_kind": "closing"}),
        ("ticket.closed", {"via": "close_command", "state_reason": "completed", "rows_total": None,
                           "rows_passed": None, "comments_unanswered": 0})])
    return number


def _to_the_brain(hub: PullHub, store: Store, repo: str, run: MainRun, role: str) -> list[int]:
    mark = red_main.NO_RESULT_MARK.format(run_id=run.run_id)
    for number in hub.issues_with_label(repo, TRIAGE_LABEL):
        issue = hub.issue(repo, number)
        if issue is not None and mark in issue.body:
            return []  # one ticket per run
    title, body = red_main.build_no_result(sha=run.sha, run_id=run.run_id, jobs=run.jobs)
    number = hub.create_issue(repo, title, body, list(red_main.NO_RESULT_LABELS))
    record.write(store, repo, number, role, None, None, [
        ("ticket.opened", {"title": title, "labels": list(red_main.NO_RESULT_LABELS), "parent": None, "weight": 2,
                           "priority": False, "write_ticket_called": False, "has_outside_case": True,
                           "acceptance_rows": 2})])
    return [number]
