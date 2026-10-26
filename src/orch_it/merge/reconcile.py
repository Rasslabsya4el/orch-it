"""The scheduled sweep of the merge flow (#400, the model's `reconcile`): `reconcile` re-reads a repo
on GitHub and repairs what no event fixed, starting no work. It follows every pull request a parked
ticket waits on (`pr_ended`) and every member of the merge line, so a merged pull request whose
follow never ran closes its ticket (MERGE-9), a stuck check is cancelled and run again, a pull
request open too long goes to the brain; then it runs the main check, so red main without a ticket
gets one and green main closes it.

It runs the same code wherever it runs: on its schedule in the workflow `reconcile`, and on the
machine when Actions are off or refused (it says whether they are on, and runs anyway). On the
machine (`bring_events`), it also brings into the store the events the workflows wrote in their
own runs' stores (their `orch-it-events-*` artifacts): each file of a store's day folder copied
once, under its own name.

Each repair is one code of `repair`: `closed` (a ticket closed), `unparked` (a ticket returned to
ready or sent to the brain), `rerun` (a run run again or cancelled to be), `red_ticket_opened`. A
pull request it cannot follow is an `error.raised` and the sweep goes on. Events: the `reconcile`
step (`run`; ended `repairs`, `actions`, `imported`), and every follow's and the main check's own.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from orch_it.events.store import Store
from orch_it.merge import marks, record
from orch_it.merge.follow import FollowError, FollowReport, follow
from orch_it.merge.hub import PullHub
from orch_it.merge.main_check import MainReport, check_main
from orch_it.merge.settings import MergeSettings
from orch_it.testruns.github import DEFAULT_REQUIRED_JOBS, GithubError
from orch_it.tickets import park
from orch_it.tickets.model import parse

ROLE = "script"
# the store's own layout: `<YYYY-MM-DD>/<writer id>.jsonl` (orch_it.events.store)
_STORE_FILE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}/[A-Za-z0-9._-]{1,120}\.jsonl")
_REPAIR = {"closed": "closed", "rerun": "rerun", "stuck": "rerun"}
_UNPARKED = ("after_merge", "red", "red_on_main", "red_in_a_row", "conflict", "edited", "commented", "unanswered",
             "timeout", "closed_unmerged", "no_result", "acceptance_missing", "row_failed", "ticket_changed")


@dataclass(frozen=True)
class ReconcileReport:
    actions: bool
    repairs: list[tuple[str, str]] = field(default_factory=list)  # (repair code, what: `#<pr>` or `#<ticket>`)
    followed: list[FollowReport] = field(default_factory=list)
    main: MainReport | None = None
    imported: int = 0
    failed: list[str] = field(default_factory=list)


def reconcile(hub: PullHub, store: Store, repo: str, base: str, *, settings: MergeSettings, now: datetime,
              role: str = ROLE, required: tuple[str, ...] = tuple(DEFAULT_REQUIRED_JOBS), owner: str | None = None,
              bring_events: bool = False) -> ReconcileReport:
    """One sweep of `repo` (its merge line into `base`); see the module text."""

    run = f"reconcile:{repo}@{now.isoformat()}"
    record.write(store, repo, None, role, run, "reconcile", [("step.started", {"run": run})])
    actions = hub.actions_enabled(repo)
    imported = bring_workflow_events(hub, store, repo) if bring_events else 0
    owner = (owner or repo.split("/")[0]).lower()
    repairs, followed, failed = [], [], []
    for number in _to_follow(hub, repo, base, owner):
        try:
            done = follow(hub, store, repo, number, base=base, settings=settings, now=now, role=role,
                          required=required, owner=owner)
        except (GithubError, FollowError, park.ParkError) as e:
            failed.append(f"#{number}: {e}"[:200])
            record.write(store, repo, None, role, run, None, [
                ("error.raised", {"component": "reconcile", "error_code": getattr(e, "code", "error"),
                                  "failed_open": False})])
            continue
        followed.append(done)
        code = _REPAIR.get(done.outcome) or ("unparked" if done.outcome in _UNPARKED else None)
        if code:
            repairs.append((code, f"#{number}"))
    main = check_main(hub, store, repo, base, settings=settings, now=now, role=role)
    repairs += [("red_ticket_opened", f"#{n}") for n in main.opened if main.outcome == "red"]
    repairs += [("closed", f"#{n}") for n in main.closed]
    record.write(store, repo, None, role, run, "reconcile", [("step.ended", {
        "outcome": "failed" if failed else "passed", "repairs": [code for code, _ in repairs], "actions": actions,
        "imported": imported})])
    return ReconcileReport(actions, repairs, followed, main, imported, failed)


def _to_follow(hub: PullHub, repo: str, base: str, owner: str) -> list[int]:
    """The pull requests a parked ticket waits on, and every member of the merge line."""

    numbers = set()
    for ticket in hub.parked(repo):
        issue = hub.issue(repo, ticket)
        held = park.held_park(parse(issue.body).park_lines) if issue is not None and issue.open else None
        ref = held.ticket if held is not None and held.kind == "pr_ended" else None
        if ref is not None and (ref.repo is None or ref.repo.lower() == repo.lower()):
            numbers.add(ref.number)
    for pull in hub.open_pulls(repo, base):
        in_line = marks.ready_since(marks.of_layer(hub.pull_comments(repo, pull.number), owner)) is not None
        if in_line or pull.auto_merge is not None:
            numbers.add(pull.number)
    return sorted(numbers)


def bring_workflow_events(hub: PullHub, store: Store, repo: str) -> int:
    """Copy into the store every events file of the workflows' artifacts it does not hold yet;
    returns how many files were copied."""

    copied = 0
    for run_id, name in hub.event_artifacts(repo):
        with tempfile.TemporaryDirectory(prefix="orch-it-events-") as folder:
            hub.download_artifact(repo, run_id, name, Path(folder))
            for path in sorted(Path(folder).rglob("*.jsonl")):
                relative = path.relative_to(folder).as_posix()
                target = Path(store.root) / relative
                if not _STORE_FILE.fullmatch(relative) or target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                copied += 1
    return copied
