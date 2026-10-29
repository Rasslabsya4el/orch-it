"""Row 6 live (#411 unit 11d): the first half runs on GitHub. A pull request merges with its follow workflow off, the
ticket is read open and parked, `reconcile` is dispatched on the base and its run waited for, and the ticket is read
back closed. The second half is the local sweep with Actions off (`merge_trial._swept`), as in `row_6`.

`row_6_live` has the signature of `row_6` and the same return shape (`workflow_off`, `actions_off`, `passed`).
"""

from __future__ import annotations

from datetime import datetime

import merge_trial
from merge_trial_live import dispatch_workflow, wait_workflow_run


def row_6_live(trial: merge_trial.Trial, hub, store, clock, sleep, plan: merge_trial.Plan | None = None) -> dict:
    """Row 6 with `reconcile` run on GitHub for the first half; the second half is the local sweep, Actions off."""

    run = merge_trial._Run(trial, hub, store, clock, sleep, plan, "r6", live=True)
    out: dict = {}
    with run.without(merge_trial.FOLLOW_WORKFLOW) as off:
        out["workflow_off"] = _dispatched(run, off)
    with run.without(merge_trial.FOLLOW_WORKFLOW) as off:
        out["actions_off"] = merge_trial._swept(run, "actions", actions=False, workflow_off=off)
    out["passed"] = out["workflow_off"]["passed"] and out["actions_off"]["passed"]
    return out


def _dispatched(run: merge_trial._Run, off: bool) -> dict:
    """A pull request merged and never followed, then `reconcile` dispatched on GitHub and its run ended. A dispatch
    refused by GitHub, or a run that does not end in time, is the half's failure: recorded, and row 6 goes on."""

    ticket, _, opened = run.pull_request("live", {f"trial/{run.tag}/live.txt": "live\n"})
    merged = run.land(opened.pr)
    before = run.read(ticket, opened.pr)
    since: datetime = run.clock()
    found = {"pr": opened.pr, "ticket": ticket, "workflow_off": off,
             "ticket_open_before": before["open"], "ticket_parked_before": before["parked"],
             "dispatched_at": since.isoformat()}
    try:
        dispatch_workflow(run, merge_trial.RECONCILE_WORKFLOW)
        ended = wait_workflow_run(run, merge_trial.RECONCILE_WORKFLOW, since, event="workflow_dispatch")
    except (merge_trial.TrialTimeout, merge_trial.GithubError) as e:
        found |= {"error": str(e)[:200], "passed": False}
        return found
    after = run.read(ticket, opened.pr)
    found |= {"run_id": ended.run_id, "run_conclusion": ended.conclusion, "run_event": ended.event,
              "run_jobs_started": ended.jobs_started, "ticket_open_after": after["open"],
              "state_reason": after["state_reason"], "closing_comment": after["comment"][:200],
              "merge_sha": (merged.merge_sha or "")[:12], "repairs": "not read (live)"}
    found["passed"] = bool(
        before["open"] and before["parked"] and ended.conclusion == "success"
        and not after["open"] and after["state_reason"] == "completed")
    return found
