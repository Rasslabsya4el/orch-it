"""Row 5 live: a merge turns the base red, the main check opens one red-on-main ticket, a rerun opens none, the fix's
green run closes it. Unlike `row_5`, the main-check workflow (`merge_trial.MAIN_CHECK_WORKFLOW`) stays on and this row
never calls `check_main` or turns a workflow off: the harness lands the pull requests (`_Run.land`), and after each run
of the test workflow (`merge_trial.WORKFLOW_FILE`) on the base it waits, through `wait_workflow_run`, for the main-check
run that follows it to end, then reads the tickets back from GitHub.

A main-check run is matched by its creation (the moment taken before the step's event), its event `workflow_run`, its
head branch, which is the base (`branch=`), and its head sha, which is the sha the step's test run was for on the base:
the step's merge sha (a rerun's is the same sha). A pull request's own test run starts a skipped main-check run of its
own branch, and a pull request's run can also list a skipped run of the base's branch with the base head from before
the merge, all inside the same window; none of those is the step's run. A run that ends `skipped` is not a pass; a
step whose run ends `failure` or `cancelled` fails too, and the row stops there with that run's id in `failed_step`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

import merge_trial
import merge_trial_live as live
from orch_it import tickets
from orch_it.events.store import Store
from orch_it.merge.hub import PASSED, PullHub


class _StepFailed(Exception):
    """A main-check run that did not end `success`: the row stops at that step and reports the run."""

    def __init__(self, record: dict):
        super().__init__(record)
        self.record = record


def row_5_live(trial: merge_trial.Trial, hub: PullHub, store: Store, clock: Callable[[], datetime],
               sleep: Callable[[float], None], plan: merge_trial.Plan | None = None) -> dict:
    """Row 5 live: a merge turns the base red, the main check opens one ticket, a rerun opens none, green closes it.
    The same keys as `row_5`; `passed` also needs the three main-check runs to have ended `success`.

    The base must be the repo's default branch, because the main check runs only on it. The CLI's preflight refuses
    a default base unless `--allow-default-base` is given, so row 5 live needs `--allow-default-base`. The row reads
    the default branch from that preflight (`trial.default_branch`) and does not read the repo again for it."""

    if trial.default_branch != trial.base:
        raise merge_trial.TrialError(
            f"row 5 live needs the base {trial.base} to be the default branch of {trial.repo}, which the repo says is "
            f"{trial.default_branch!r}: `{merge_trial.MAIN_CHECK_WORKFLOW}` runs only on the default branch")
    run = merge_trial._Run(trial, hub, store, clock, sleep, plan, "r5", live=True)
    out: dict = {"main_check_runs": [], "passed": False}
    try:
        _steps(run, out)
    except _StepFailed as failed:
        out["failed_step"] = failed.record
        out["passed"] = False
    return out


def _steps(run: merge_trial._Run, out: dict) -> None:
    trial, hub = run.trial, run.hub
    path = f"tests/test_trial_main_red_{run.tag}.py"

    with _main_check_step(run, out, "first") as head:
        ticket, branch, opened = run.pull_request("culprit", {path: merge_trial.RED_ON_PUSH})
        merged = run.land(opened.pr)
        head.sha = merged.merge_sha
        run.follow(opened.pr)
        first = run.ended_run(merged.merge_sha)
        out["red_run"] = {"run_id": first.run_id, "sha": first.sha[:12], "jobs": dict(first.jobs)}
        ids = frozenset(hub.failing_ids(run.repo, first.run_id) or [])
        if not ids:
            raise merge_trial.TrialError(f"the push run {first.run_id} at {first.sha[:12]} is not red with a report: "
                                         f"the trial test {path} must fail on a push (jobs {first.jobs}) - the base "
                                         "stays as it is")
        out["failing_ids"] = sorted(ids)
    ours = run.red_tickets_of(ids)
    for number in ours:
        trial.adopt(number)
    first_check: dict = {"main_check_run": out["main_check_runs"][-1], "red_tickets": ours}
    shape = None
    if len(ours) == 1:
        issue = hub.issue(run.repo, ours[0])
        shape = len(tickets.violations(tickets.parse(issue.body, title=issue.title, labels=issue.labels)))
        first_check |= {"shape_violations": shape, "labels": sorted(issue.labels),
                        "names_the_pull_request": f"#{opened.pr}" in issue.body}
    out["first_check"] = first_check
    run.say(f"first main check: red tickets {ours}")

    with _main_check_step(run, out, "second") as head:
        run.say(f"rerun of the run {first.run_id}")
        hub.rerun(run.repo, first.run_id)
        again = run.ended_run(first.sha, attempt=first.attempt + 1)
        head.sha = first.sha
    # the rerun must open none: what the list holds after the settle is what counts, not a wait for a ticket
    after = run.red_tickets_of(ids, settle=True)
    for number in after:
        trial.adopt(number)
    out["second_check"] = {"main_check_run": out["main_check_runs"][-1], "attempt": again.attempt,
                           "opened": [number for number in after if number not in ours], "red_tickets": after}
    run.say(f"second main check: red tickets {after}")

    with _main_check_step(run, out, "third") as head:
        fix_ticket, fix_branch, fixed = run.pull_request("fix", {path: merge_trial.GREEN_ON_PUSH})
        fix_merge = run.land(fixed.pr)
        head.sha = fix_merge.merge_sha
        run.follow(fixed.pr)
        green = run.ended_run(fix_merge.merge_sha)
    closed = [number for number in ours if not hub.issue(run.repo, number).open
              and hub.issue(run.repo, number).state_reason == "completed"]
    out["third_check"] = {"main_check_run": out["main_check_runs"][-1], "run_id": green.run_id, "closed": closed}
    run.say(f"third main check: closed {closed}")

    out["passed"] = bool(
        len(ours) == 1 and shape == 0 and first_check["names_the_pull_request"]
        and out["second_check"]["opened"] == [] and out["second_check"]["red_tickets"] == ours
        and closed == ours and green.jobs and set(green.jobs.values()) == {PASSED}
        and [record["conclusion"] for record in out["main_check_runs"]] == ["success"] * 3)


@dataclass
class _Step:
    """The head sha of one step's main-check run: the sha the step's test run was for on the base. The step's block
    sets it before the block ends."""

    sha: str | None = None


@contextmanager
def _main_check_step(run: merge_trial._Run, out: dict, step: str) -> Iterator[_Step]:
    """One step of the row: `since` is taken before the block; the block sets the step's head sha on the holder it
    gets, and when the block ends without an error the main-check run with that head sha, created since `since`, is
    waited for and recorded (`_main_check`). A block that set no sha is an error, found before any wait, never a
    fallback to any run. An error in the block ends the step before any wait."""

    since = run.clock()
    head = _Step()
    yield head
    if head.sha is None:
        raise merge_trial.TrialError(f"the {step} step ended with no head sha set: its main-check run cannot be picked "
                                     "out, and no run is taken in its place")
    _main_check(run, out, step, since, head.sha)


def _main_check(run: merge_trial._Run, out: dict, step: str, since: datetime, head_sha: str) -> None:
    """Wait for the main-check run that `since`, `workflow_run`, the base's branch and `head_sha` pick out to end;
    `success` or _StepFailed. A skipped run with `head_sha` is returned as skipped, and fails the step."""

    found = live.wait_workflow_run(run, merge_trial.MAIN_CHECK_WORKFLOW, since, head_sha=head_sha,
                                   event="workflow_run", branch=run.base)
    out["main_check_runs"].append({"step": step, "run_id": found.run_id, "conclusion": found.conclusion})
    if found.conclusion != "success":
        raise _StepFailed({"step": step, "run_id": found.run_id, "conclusion": found.conclusion})
