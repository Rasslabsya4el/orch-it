"""Row 5 live: a merge turns the base red, the main check opens one red-on-main ticket, a rerun opens none, the fix's
green run closes it. Unlike `row_5`, `main-check.yml` stays on and this row never calls `check_main` or turns a
workflow off: the harness lands the pull requests (`_Run.land`), and after each run of `tests` on the base it waits,
through `wait_workflow_run`, for the `main-check` run that follows it to end, then reads the tickets back from GitHub.

A `main-check` run is matched by its creation (`since`, the moment taken before the event that starts it) and its
event `workflow_run`, not by its head sha: a run started by `workflow_run` takes the default branch's head when it
starts, which the row does not know. A run that ends `skipped` is not a pass; a step whose run ends `failure` or
`cancelled` fails too, and the row stops there with that run's id in `failed_step`.
"""

from __future__ import annotations

from collections.abc import Callable
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
    The same keys as `row_5`; `passed` also needs the three main-check runs to have ended `success`."""

    facts = trial.preflight()
    if facts.get("default_branch") != trial.base:
        raise merge_trial.TrialError(
            f"row 5 live needs the base {trial.base} to be the default branch of {trial.repo}, which the repo says is "
            f"{facts.get('default_branch')!r}: `{merge_trial.MAIN_CHECK_WORKFLOW}` runs only on the default branch")
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

    since = run.clock()
    ticket, branch, opened = run.pull_request("culprit", {path: merge_trial.RED_ON_PUSH})
    merged = run.land(opened.pr)
    run.follow(opened.pr)
    first = run.ended_run(merged.merge_sha)
    out["red_run"] = {"run_id": first.run_id, "sha": first.sha[:12], "jobs": dict(first.jobs)}
    ids = frozenset(hub.failing_ids(run.repo, first.run_id) or [])
    if not ids:
        raise merge_trial.TrialError(f"the push run {first.run_id} at {first.sha[:12]} is not red with a report: the "
                                     f"trial test {path} must fail on a push (jobs {first.jobs}) - the base stays as "
                                     "it is")
    out["failing_ids"] = sorted(ids)
    _main_check(run, out, "first", since)
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

    since = run.clock()
    run.say(f"rerun of the run {first.run_id}")
    hub.rerun(run.repo, first.run_id)
    again = run.ended_run(first.sha, attempt=first.attempt + 1)
    _main_check(run, out, "second", since)
    # the rerun must open none: what the list holds after the settle is what counts, not a wait for a ticket
    after = run.red_tickets_of(ids, settle=True)
    for number in after:
        trial.adopt(number)
    out["second_check"] = {"main_check_run": out["main_check_runs"][-1], "attempt": again.attempt,
                           "opened": [number for number in after if number not in ours], "red_tickets": after}
    run.say(f"second main check: red tickets {after}")

    since = run.clock()
    fix_ticket, fix_branch, fixed = run.pull_request("fix", {path: None})
    fix_merge = run.land(fixed.pr)
    run.follow(fixed.pr)
    green = run.ended_run(fix_merge.merge_sha)
    _main_check(run, out, "third", since)
    closed = [number for number in ours if not hub.issue(run.repo, number).open
              and hub.issue(run.repo, number).state_reason == "completed"]
    out["third_check"] = {"main_check_run": out["main_check_runs"][-1], "run_id": green.run_id, "closed": closed}
    run.say(f"third main check: closed {closed}")

    out["passed"] = bool(
        len(ours) == 1 and shape == 0 and first_check["names_the_pull_request"]
        and out["second_check"]["opened"] == [] and out["second_check"]["red_tickets"] == ours
        and closed == ours and green.jobs and set(green.jobs.values()) == {PASSED}
        and [record["conclusion"] for record in out["main_check_runs"]] == ["success"] * 3)


def _main_check(run: merge_trial._Run, out: dict, step: str, since: datetime) -> None:
    """Wait for the `main-check` run that `since` and `workflow_run` pick out to end; `success` or _StepFailed."""

    found = live.wait_workflow_run(run, merge_trial.MAIN_CHECK_WORKFLOW, since, event="workflow_run")
    out["main_check_runs"].append({"step": step, "run_id": found.run_id, "conclusion": found.conclusion})
    if found.conclusion != "success":
        raise _StepFailed({"step": step, "run_id": found.run_id, "conclusion": found.conclusion})
