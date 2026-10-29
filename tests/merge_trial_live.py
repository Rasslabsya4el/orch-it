"""The steps the three live rows share (#411 row 9): each of rows 4, 5 and 6 runs on a throwaway repo of its
own with the repo's workflows left on, and the row only dispatches what it needs, waits for a run to end and
reads the result. Every function takes the row's `_Run` (`tests/merge_trial.py`), so every wait is bounded by
`Plan.wait_minutes` (or the `minutes` given) and reads through `_Run.wait`.

The live rows are `row_4_live`, `row_5_live` and `row_6_live` in `tests/merge_trial_live_r4.py`,
`merge_trial_live_r5.py` and `merge_trial_live_r6.py`, each with the signature of `row_4`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import merge_trial


@dataclass(frozen=True)
class LiveRun:
    """One run of a workflow file that ended: its id, its conclusion (`success`, `failure`, `skipped`, ...),
    the event and head sha it ran on, and how many of its jobs started."""

    run_id: str
    conclusion: str | None
    event: str | None
    head_sha: str | None
    jobs_started: int


def wait_workflow_run(run: merge_trial._Run, workflow_file: str, since: datetime, *, head_sha: str | None = None,
                      event: str | None = None, branch: str | None = None, minutes: float | None = None,
                      ignore_ids: frozenset[str] = frozenset()) -> LiveRun:
    """The first run of `workflow_file` GitHub lists that was created at or after `since` (a timezone-aware
    moment), with `head_sha`, `event` and `branch` (the run's head branch) when given, and has ended; newest first.
    A run that ended `skipped` is returned as skipped, never as a success. `TrialTimeout` when none ends within the
    bound."""

    def probe() -> LiveRun | None:
        for listed in run.trial.workflow_runs(workflow_file):
            if str(listed.get("id")) in ignore_ids:
                continue
            created = _moment(listed.get("created_at"))
            if created is None or created < since.replace(microsecond=0):  # GitHub's times have whole seconds
                continue
            if head_sha is not None and listed.get("head_sha") != head_sha:
                continue
            if event is not None and listed.get("event") != event:
                continue
            if branch is not None and listed.get("head_branch") != branch:
                continue
            if listed.get("status") != "completed":
                continue
            return _ended(run, listed)
        return None

    found = run.wait(f"a run of {workflow_file} created since {since:%H:%M:%S}", probe, minutes=minutes)
    run.say(f"{workflow_file}: run {found.run_id} ended {found.conclusion} ({found.jobs_started} job(s) started)")
    return found


def dispatch_workflow(run: merge_trial._Run, workflow_file: str) -> None:
    """`gh workflow run <file>` on the row's repo and base; returns None."""

    run.trial.dispatch_workflow(workflow_file)
    run.say(f"{workflow_file}: dispatched on {run.base}")


def _ended(run: merge_trial._Run, listed: dict) -> LiveRun:
    """A listed run that ended, with the jobs of it that started: the check runs of its head with its run id."""

    run_id = str(listed.get("id"))
    if listed.get("conclusion") == "skipped":  # a skipped run starts no job, whatever its checks carry
        started = 0
    else:
        checks = run.hub.checks(run.repo, listed.get("head_sha") or "")
        started = sum(1 for check in checks if check.run_id == run_id and check.started_at is not None)
    return LiveRun(run_id=run_id, conclusion=listed.get("conclusion"), event=listed.get("event"),
                   head_sha=listed.get("head_sha"), jobs_started=started)


def _moment(text: object) -> datetime | None:
    """An ISO 8601 time GitHub wrote (`...Z`) as a timezone-aware datetime; None when it is not one."""

    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
