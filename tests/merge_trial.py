"""The live trial of the merge flow (#400 acceptance rows 2 to 6) on a real repo: the owner runs it
once against a throwaway repo, or a protected branch, and its JSON is what the result comment posts.

    python tests/merge_trial.py --repo OWNER/NAME --base BRANCH --work-tree PATH [--rows 2,3,4,5,6] [--out FILE]

Each row is a function of a `Trial` (what is not the GitHub API: tickets, branches, commits,
closing a pull request, a workflow or Actions turned off), a `PullHub`, the store, a clock and a
`sleep`, and of nothing else but the merge flow's own functions (`open_pr`, `run_line`, `follow`,
`check_main`, `reconcile`). It returns the numbers the comment posts, with `passed`. Because the
steps touch no `gh` or git themselves, the tests run them on `tests/fake_github.py` and a
`FakeTrial` (`tests/test_merge_trial.py`); `RealTrial` is the adapter of a real clone.

- row 2: four pull requests (each its own file) ready within one minute, then `run_line`: all four
  merge, in the order of their ready marks, each after one up to date check run on the head it
  merged at; per pull request the minutes it waited, its check runs and whether its head was up
  to date when they started (the polls of the line are watched: a head's first poll with checks
  gives `Pull.behind`);
- row 3, the same run: a fifth ticket whose branch adds the file the first one adds with other text.
  The two are opened first, so whichever the line serves second conflicts once the other merged,
  with the three others ready behind it: its ticket goes back to ready with the file in its last
  comment, `attempt.counted` false, and the pull requests behind it merge;
- row 4: five real pull requests, one per end, each followed (`follow`, with the `follow` workflow
  disabled so the local follow is the one that acts) and read back from GitHub and the store: merged,
  merged with an `after merge` row, red (a failing test file), closed unmerged, a person's comment;
- row 5: a pull request adds a test that fails only in a `push` run (green on the pull request),
  so its merge turns the base red; `check_main` opens one red-on-main ticket in #398's shape, a
  second one on the rerun of the same run opens none, a pull request removing the test turns the
  base green and the next `check_main` closes the ticket;
- row 6: with the `follow` workflow disabled a pull request merges and `reconcile` closes its
  ticket; then the same with Actions turned off, the sweep running here (`actions=False`).

Every wait is bounded (`Plan.wait_minutes`, the line's own bounds in `Plan.settings`) and polls
every `poll_seconds`. A workflow or Actions turned off is turned on again when its row ends and
by `RealTrial.restore` when the script ends (every setting is restored even when one restore
fails). Before any row, `RealTrial.preflight` refuses a clone that is not the repo's (its origin
names exactly OWNER/NAME) or has changes to tracked files, a repo without a `tests`
workflow, a base that does not exist, is not protected by strict checks `linux` and `windows` only,
or is the repo's default branch (row 5 puts a red test on the base; `--allow-default-base`). When
the rows end, every trial ticket still open is closed `not_planned` (`ready-for-agent` off first,
so no agent takes one) and every trial pull request still open is closed; `--keep` leaves them.
The base ends with the trial's files on it (row 5 leaves it green); a branch that must keep nothing
is the owner's to reset.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from ticket_fixtures import fixture

from orch_it import tickets
from orch_it.events import schema
from orch_it.events.store import Store
from orch_it.merge import git as merge_git
from orch_it.merge import marks, red_main
from orch_it.merge.gh import GhPullHub
from orch_it.merge.follow import FollowReport, follow
from orch_it.merge.hub import FAILED, PASSED, PENDING, Pull, PullHub
from orch_it.merge.line import run_line
from orch_it.merge.main_check import check_main
from orch_it.merge.pr_open import Opened, open_pr
from orch_it.merge.reconcile import reconcile
from orch_it.merge.settings import MergeSettings
from orch_it.testruns.config import DEFAULT_REQUIRED_CHECKS
from orch_it.testruns.github import WORKFLOW_FILE, Gh, GithubError, run_gh
from orch_it.tickets.model import READY_LABEL
from orch_it.tickets.reader import TRIAGE_LABEL

if __name__ == "__main__":
    # run as a script this module is `__main__`; the live module's `import merge_trial` must get this same
    # object, or TrialError and _Run would exist twice
    sys.modules["merge_trial"] = sys.modules[__name__]

TITLE, FULL_BODY = fixture("good_basic")
AFTER_MERGE_ROW = "| 4 after merge | abandoned carts at the payment step | under 30 % in the weekly report |\n"
BODY = FULL_BODY.replace(AFTER_MERGE_ROW, "")
assert BODY != FULL_BODY
ROWS_FULL = """| row | result | evidence |
|---|---|---|
| 1 | pass | 4.90 = 4.90 |
| 2 | pass | 5.10 = 5.10 |
| 3 | pass | 7.30 = 7.30 |
| 4 | after merge | the weekly report |
"""
ROWS = "\n".join(line for line in ROWS_FULL.splitlines() if not line.startswith("| 4 ")) + "\n"
REQUIRED = DEFAULT_REQUIRED_CHECKS
FOLLOW_WORKFLOW, MAIN_CHECK_WORKFLOW, RECONCILE_WORKFLOW = "follow.yml", "main-check.yml", "reconcile.yml"
LIST_MINUTES = 3  # the longest a read of a list GitHub fills eventually waits for what must appear in it
SETTLE_SECONDS = 60  # the fixed wait before a list is read once for what must NOT appear in it
LIVE_ROW_MODULES = {4: "merge_trial_live_r4", 5: "merge_trial_live_r5", 6: "merge_trial_live_r6"}
RED_ON_PR = "def test_red():\n    assert False, 'the trial: a pull request that is red'\n"
# green in the pull request's run, red in the push run after its merge: a merge that turns the base red
# red in a run of the base (a push, or the run `--dispatch-base-runs` asks for), green on a pull request
RED_ON_PUSH = ("import os\n\n\ndef test_red_on_a_push():\n"
               "    assert os.environ.get('GITHUB_EVENT_NAME') not in ('push', 'workflow_dispatch'), "
               "'the trial: red only on the base'\n")
# the fix of that merge: the same test, passing, so the next push run runs it and holds it passed (a red-on-main ticket
# closes only on a run whose reports hold each of its ids passed, #414 unit 4; a deleted test is never run again)
GREEN_ON_PUSH = "def test_red_on_a_push():\n    pass\n"
ROWS_ALL = (2, 3, 4, 5, 6)
OPEN_WORKERS = 4
READY_WITHIN_S = 60


class TrialError(RuntimeError):
    """A step of the trial that cannot go on (a wait that ran out, a run that is not what the row needs)."""


class TrialTimeout(TrialError):
    pass


class Trial(Protocol):
    """What the rows need that is not the GitHub API; `RealTrial` over a clone, `FakeTrial` in the tests."""

    repo: str
    base: str
    git: Callable[[list[str]], str]  # `git <args>` in the work tree (for `open_pr`)
    lock_root: Path | None  # the merge line's lock folder (None: the default one)
    default_branch: str | None  # the repo's default branch as its preflight read it (None before a preflight)

    def preflight(self) -> dict: ...

    def make_ticket(self, title: str, body: str) -> int:
        """An open issue with the label `ready-for-agent`; its number."""

    def make_branch(self, name: str, files: dict[str, str | None]) -> str:
        """Branch `name` off the base's tip with `files` written (None: removed), committed and pushed; its head."""

    def close_pull(self, pr: int) -> None: ...

    def comment_as_person(self, pr: int, text: str) -> None:
        """The owner's plain comment on a pull request, as themself, without the layer's mark."""

    def disable_workflow(self, file: str) -> None: ...

    def enable_workflow(self, file: str) -> None: ...

    def set_actions(self, on: bool) -> None: ...

    def dispatch_tests(self) -> None:
        """Ask for a run of `tests` on the base when the base gets none of its own on a push (a trial
        branch `tests.yml` does not name under `push`); nothing otherwise."""

    def dispatch_workflow(self, file: str) -> None:
        """`gh workflow run <file>` on the base; returns None."""

    def workflow_runs(self, file: str) -> list[dict]:
        """The runs GitHub lists for the workflow `file`, newest first: the gh JSON of each run."""

    def rate_remaining(self) -> int:
        """The core REST quota GitHub has left (`gh api rate_limit`), read once at the end of a run."""

    def adopt(self, ticket: int) -> None:
        """A ticket the merge flow opened during the trial (a red-on-main one): `cleanup` closes it too."""

    def cleanup(self) -> dict:
        """Close every trial ticket and pull request still open; what was closed (and what failed)."""

    def restore(self) -> None:
        """Turn on again whatever this trial turned off."""


@dataclass(frozen=True)
class Plan:
    settings: MergeSettings = MergeSettings(line_minutes=90, lock_stale_minutes=180)
    wait_minutes: float = 45  # the longest any one wait (checks, a run on the base) lasts
    owner: str | None = None  # the login of the layer's comments (default: the repo's owner)
    open_workers: int = OPEN_WORKERS  # pull requests `pr open`ed at once
    list_minutes: float = LIST_MINUTES  # the longest a read of an eventually consistent list waits for its ticket


# --- the rows -----------------------------------------------------------------------------------------


def row_2(trial: Trial, hub: PullHub, store: Store, clock: Callable[[], datetime], sleep: Callable[[float], None],
          plan: Plan | None = None) -> dict:
    """Row 2: see `line_rows`; this is the numbers of the four pull requests that merge."""

    return line_rows(trial, hub, store, clock, sleep, plan)[0]


def row_3(trial: Trial, hub: PullHub, store: Store, clock: Callable[[], datetime], sleep: Callable[[float], None],
          plan: Plan | None = None) -> dict:
    """Row 3: see `line_rows`; this is the numbers of the pull request that conflicts."""

    return line_rows(trial, hub, store, clock, sleep, plan)[1]


def line_rows(trial: Trial, hub: PullHub, store: Store, clock: Callable[[], datetime],
              sleep: Callable[[float], None], plan: Plan | None = None) -> tuple[dict, dict]:
    """Rows 2 and 3 in one run of the line: (row 2's numbers, row 3's numbers)."""

    run = _Run(trial, hub, store, clock, sleep, plan, "r2")
    shared = f"trial/{run.tag}/shared.txt"
    own = {label: f"trial/{run.tag}/{label}.txt" for label in "abcde"}
    files = {label: {path: f"{label} {run.tag}\n"} for label, path in own.items()}
    files["a"] = {**{own["a"]: f"a {run.tag}\n"}, shared: "from a\n"}
    files["e"] = {**{own["e"]: f"e {run.tag}\n"}, shared: "from e\n"}
    prepared = {label: run.prepare(label, changes) for label, changes in files.items()}
    opened: dict[str, Opened] = {}
    seen: dict[tuple[int, str], bool] = {}  # (pr, head) -> up to date at the first poll that saw checks on it
    last_said = [clock()]

    def watch() -> None:
        try:
            pulls = hub.open_pulls(run.repo, run.base)
            for pull in pulls:
                if (pull.number, pull.head_sha) not in seen and hub.checks(run.repo, pull.head_sha):
                    seen[(pull.number, pull.head_sha)] = pull.behind == 0
            if (clock() - last_said[0]).total_seconds() >= 300:
                last_said[0] = clock()
                run.say("line: " + ", ".join(f"#{p.number} behind {p.behind}" for p in pulls) + " still open")
        except GithubError:  # one poll missed: the head is seen at the next
            pass

    def watching_sleep(seconds: float) -> None:
        sleep(seconds)
        watch()

    try:
        # the two that share a file go first, so the three others are ready behind whichever conflicts
        for stage in (("a", "e"), ("b", "c", "d")):
            opened |= run.open_all({label: prepared[label] for label in stage}, ROWS)
        ready = {label: run.ready_at(done.pr) for label, done in opened.items()}
        run.say(f"ready within {(max(ready.values()) - min(ready.values())).total_seconds():.0f} s; the line starts")
        watch()
        line_started = clock()
        report = run_line(hub, store, run.repo, run.base, settings=run.settings, clock=clock, sleep=watching_sleep,
                          root=trial.lock_root, holder="merge trial", owner=run.owner)
        line_minutes = (clock() - line_started).total_seconds() / 60
        run.say(f"the line ended: {report.reason}, merged {report.merged}, left {report.left}")
        by_pr = {served.pr: served for served in report.served}
        pulls = {label: hub.pull(run.repo, done.pr) for label, done in opened.items()}
        order = sorted(opened, key=lambda label: (ready[label], opened[label].pr))
        pair = [label for label in order if label in ("a", "e")]
        loser = next((label for label in pair if pulls[label].state != "merged"), None)
        winners = [label for label in order if label != loser]
        records = {label: _merged_record(run, label, prepared[label][0], opened[label], pulls[label],
                                         by_pr.get(opened[label].pr), seen, ready[label]) for label in order}
        merged = sorted((label for label in winners if pulls[label].state == "merged"),
                        key=lambda label: (pulls[label].merged_at, opened[label].pr))
        for rank, label in enumerate(merged, 1):
            records[label]["order"] = rank
        row2 = {
            "tickets": len(winners), "reason": report.reason, "line_minutes": round(line_minutes, 1),
            "ready_span_s": (max(ready.values()) - min(ready.values())).total_seconds(),
            "merged": len(merged), "expected_order": [opened[label].pr for label in winners],
            "merge_order": [opened[label].pr for label in merged],
            "in_order": merged == winners, "prs": [records[label] for label in winners]}
        row2["passed"] = bool(
            len(winners) == 4 and merged == winners and row2["ready_span_s"] <= READY_WITHIN_S
            and all(r["runs_on_merged_head"] == 1 and r["attempts"] == 1 and r["up_to_date_at_run_start"] is True
                    for r in records.values() if r["merged"]))
        row3 = _conflict_record(run, loser, prepared, order, opened, pulls, by_pr, shared, len(winners))
    finally:
        for done in opened.values():
            pull = hub.pull(run.repo, done.pr)
            if pull is not None and pull.state == "open":
                trial.close_pull(done.pr)
    return row2, row3


def end_judges(tag: str) -> dict[str, Callable[[dict], bool]]:
    """The judge of each end of row 4: the ticket as GitHub holds it after the end (`_Run.read`) says the end's
    outcome. Shared by `row_4` and its live form (`tests/merge_trial_live_r4.py`), so both judge alike."""

    return {
        "closed_unmerged": lambda r: (
            r["needs_triage"] and not r["ready"] and not r["parked"] and r["open"] and r["pr_state"] == "closed"),
        "comment": lambda r: (
            not r["in_line"] and r["needs_triage"] and not r["ready"] and not r["parked"]
            and r["pr_state"] == "open"),
        "merged": lambda r: (
            not r["open"] and r["state_reason"] == "completed" and r["pr_state"] == "merged"
            and all(f"| {row} | pass |" in r["comment"] for row in "123")),
        "after_merge": lambda r: (
            r["open"] and r["ready"] and not r["parked"] and r["pr_state"] == "merged" and "row 4" in r["comment"]),
        "red": lambda r: (
            r["open"] and r["ready"] and not r["parked"] and f"test_trial_pr_red_{tag}" in r["comment"]),
    }


def row_4(trial: Trial, hub: PullHub, store: Store, clock: Callable[[], datetime], sleep: Callable[[float], None],
          plan: Plan | None = None) -> dict:
    """Row 4: the ticket follows its pull request, every end, read back from GitHub and the store."""

    run = _Run(trial, hub, store, clock, sleep, plan, "r4")
    judge = end_judges(run.tag)
    out: dict = {}
    opened: list[int] = []
    with run.without(FOLLOW_WORKFLOW) as off:
        out["follow_workflow_disabled"] = off
        try:
            # the red pull request first: its checks run while the others are done
            red_file = f"tests/test_trial_pr_red_{run.tag}.py"
            red = run.pull_request("red", {red_file: RED_ON_PR})
            opened.append(red[2].pr)
            closed = run.pull_request("closed", {f"trial/{run.tag}/closed.txt": "closed\n"})
            opened.append(closed[2].pr)
            trial.close_pull(closed[2].pr)
            out["closed_unmerged"] = _ending(run, closed, "closed_unmerged", judge["closed_unmerged"])
            said = run.pull_request("comment", {f"trial/{run.tag}/comment.txt": "comment\n"})
            opened.append(said[2].pr)
            trial.comment_as_person(said[2].pr, "Wait, please look at the zones before this lands.")
            out["comment"] = _ending(run, said, "commented", judge["comment"], wanted_event="pr.commented")
            merged = run.pull_request("merged", {f"trial/{run.tag}/merged.txt": "merged\n"})
            opened.append(merged[2].pr)
            run.land(merged[2].pr)
            out["merged"] = _ending(run, merged, "closed", judge["merged"], wanted_event="ticket.closed")
            later = run.pull_request("after-merge", {f"trial/{run.tag}/later.txt": "later\n"}, body=FULL_BODY,
                                     rows=ROWS_FULL)
            opened.append(later[2].pr)
            run.land(later[2].pr)
            out["after_merge"] = _ending(run, later, "after_merge", judge["after_merge"],
                                         unwanted_event="ticket.closed")
            run.wait("the red pull request's checks to end", lambda: run.ended_red(red[2].pr))
            out["red"] = _ending(run, red, "red", judge["red"], wanted_event="attempt.counted", counted=True)
        finally:
            for pr in opened:
                pull = hub.pull(run.repo, pr)
                if pull is not None and pull.state == "open":
                    trial.close_pull(pr)
    ends = ("merged", "after_merge", "red", "closed_unmerged", "comment")
    out["passed"] = all(end in out and out[end]["passed"] for end in ends)
    return out


def row_5(trial: Trial, hub: PullHub, store: Store, clock: Callable[[], datetime], sleep: Callable[[float], None],
          plan: Plan | None = None) -> dict:
    """Row 5: a merge turns the base red, `check_main` opens one ticket, a rerun opens none, green closes it."""

    run = _Run(trial, hub, store, clock, sleep, plan, "r5")
    out: dict = {}
    path = f"tests/test_trial_main_red_{run.tag}.py"
    with run.without(MAIN_CHECK_WORKFLOW) as off:
        out["main_check_workflow_disabled"] = off
        ticket, branch, opened = run.pull_request("culprit", {path: RED_ON_PUSH})
        merged = run.land(opened.pr)
        run.follow(opened.pr)
        first = run.ended_run(merged.merge_sha)
        out["red_run"] = {"run_id": first.run_id, "sha": first.sha[:12], "jobs": dict(first.jobs)}
        ids = frozenset(hub.failing_ids(run.repo, first.run_id) or [])
        if not ids:
            raise TrialError(f"the push run {first.run_id} at {first.sha[:12]} is not red with a report: the "
                             f"trial test {path} must fail on a push (jobs {first.jobs}) - the base stays as it is")
        out["failing_ids"] = sorted(ids)
        one = check_main(hub, store, run.repo, run.base)
        run.adopt(one)
        run.say(f"main check: {one.outcome} opened {one.opened}")
        ours = run.red_tickets_of(ids)
        out["first_check"] = {"outcome": one.outcome, "opened": one.opened, "red_tickets": ours}
        shape = None
        if len(ours) == 1:
            issue = hub.issue(run.repo, ours[0])
            shape = len(tickets.violations(tickets.parse(issue.body, title=issue.title, labels=issue.labels)))
            out["first_check"] |= {"shape_violations": shape, "labels": sorted(issue.labels),
                                   "names_the_pull_request": f"#{opened.pr}" in issue.body}
        run.say(f"rerun of the run {first.run_id}")
        hub.rerun(run.repo, first.run_id)
        again = run.ended_run(first.sha, attempt=first.attempt + 1)
        two = check_main(hub, store, run.repo, run.base)
        run.adopt(two)
        # the rerun must open none: what the list holds after the settle is what counts, not a wait for a ticket
        out["second_check"] = {"outcome": two.outcome, "attempt": again.attempt, "opened": two.opened,
                               "red_tickets": run.red_tickets_of(ids, settle=True)}
        run.say(f"second main check: {two.outcome} opened {two.opened}")
        fix_ticket, fix_branch, fixed = run.pull_request("fix", {path: GREEN_ON_PUSH})
        fix_merge = run.land(fixed.pr)
        run.follow(fixed.pr)
        green = run.ended_run(fix_merge.merge_sha)
        three = check_main(hub, store, run.repo, run.base)
        run.adopt(three)
        closed = [n for n in ours if not hub.issue(run.repo, n).open
                  and hub.issue(run.repo, n).state_reason == "completed"]
        out["third_check"] = {"outcome": three.outcome, "run_id": green.run_id, "closed_by_this_check": three.closed,
                              "closed": closed}
        run.say(f"third main check: {three.outcome} closed {three.closed}")
    out["passed"] = bool(
        len(ours) == 1 and shape == 0 and out["first_check"].get("names_the_pull_request")
        and out["second_check"]["opened"] == [] and out["second_check"]["red_tickets"] == ours
        and closed == ours and green.jobs and set(green.jobs.values()) == {PASSED})
    return out


def row_6(trial: Trial, hub: PullHub, store: Store, clock: Callable[[], datetime], sleep: Callable[[float], None],
          plan: Plan | None = None) -> dict:
    """Row 6: `reconcile` closes the ticket of a merged pull request whose follow never ran, with Actions on and off."""

    run = _Run(trial, hub, store, clock, sleep, plan, "r6")
    out: dict = {}
    with run.without(FOLLOW_WORKFLOW) as off:
        out["workflow_off"] = _swept(run, "workflow", actions=None, workflow_off=off)
    with run.without(FOLLOW_WORKFLOW) as off:
        out["actions_off"] = _swept(run, "actions", actions=False, workflow_off=off)
    out["passed"] = out["workflow_off"]["passed"] and out["actions_off"]["passed"]
    return out


def _swept(run: _Run, label: str, *, actions: bool | None, workflow_off: bool) -> dict:
    """A pull request merged and never followed, then one `reconcile`; `actions` False: Actions are off for it."""

    ticket, branch, opened = run.pull_request(label, {f"trial/{run.tag}/{label}.txt": f"{label}\n"})
    merged = run.land(opened.pr)
    before = run.read(ticket, opened.pr)
    run.say(f"{label}: #{opened.pr} merged, ticket #{ticket} open={before['open']}; reconcile")
    found: dict = {"pr": opened.pr, "ticket": ticket, "workflow_off": workflow_off,
                   "ticket_open_before": before["open"], "ticket_parked_before": before["parked"]}
    done = None
    try:
        if actions is False:
            run.trial.set_actions(False)
        try:
            done = reconcile(run.hub, run.store, run.repo, run.base, settings=run.settings, now=run.clock(),
                             owner=run.owner)
        except GithubError as e:  # the sweep of a repo whose Actions are off may be refused part of the way
            found["error"] = str(e)[:300]
    finally:
        if actions is False:
            run.trial.set_actions(True)
    after = run.read(ticket, opened.pr)
    found |= {"ticket_open_after": after["open"], "state_reason": after["state_reason"],
              "closing_comment": after["comment"][:200], "merge_sha": (merged.merge_sha or "")[:12]}
    if done is not None:
        found |= {"actions": done.actions, "repairs": [list(r) for r in done.repairs], "failed": done.failed,
                  "main": done.main.outcome if done.main else None}
    found["passed"] = bool(
        before["open"] and before["parked"] and not after["open"] and after["state_reason"] == "completed"
        and done is not None and ("closed", f"#{opened.pr}") in done.repairs
        and (actions is None or done.actions is False))
    return found


ROW_FUNCTIONS = {4: row_4, 5: row_5, 6: row_6}
LIVE_ROW_FUNCTIONS: dict[int, Callable] = {}  # filled lazily by `live_row_function`, one module per row


def live_row_function(number: int) -> Callable:
    """The live form of row `number` (`row_<n>_live` of its module in `LIVE_ROW_MODULES`), imported on first use;
    a module that is not there yet is a `TrialError` naming it."""

    if number not in LIVE_ROW_FUNCTIONS:
        module = LIVE_ROW_MODULES[number]
        try:
            found = importlib.import_module(module)
        except ModuleNotFoundError as e:
            if e.name != module:  # a module the live row imports is missing, not the row's own
                raise
            raise TrialError(f"--live: row {number} has no live form yet (tests/{module}.py is not there)") from None
        if not callable(getattr(found, f"row_{number}_live", None)):
            raise TrialError(f"--live: tests/{module}.py has no row_{number}_live function")
        LIVE_ROW_FUNCTIONS[number] = getattr(found, f"row_{number}_live")
    return LIVE_ROW_FUNCTIONS[number]


# --- the numbers of rows 2 and 3 ---------------------------------------------------------------------------


def _merged_record(run: _Run, label: str, ticket: int, done: Opened, pull: Pull, served, seen: dict,
                   ready: datetime) -> dict:
    checks = run.hub.checks(run.repo, pull.head_sha)
    runs = sorted({c.run_id for c in checks if c.run_id})
    return {
        "label": label, "pr": done.pr, "ticket": ticket, "merged": pull.state == "merged",
        "merged_at": pull.merged_at.isoformat() if pull.merged_at else None, "ready_at": ready.isoformat(),
        "line_result": served.result if served else None, "line_detail": served.detail if served else None,
        "waited_minutes": served.waited_minutes if served else None, "updates": served.updates if served else None,
        "merged_head": pull.head_sha[:12], "runs_on_merged_head": len(runs),
        "attempts": max((c.attempt for c in checks), default=0), "checks": sorted(c.name for c in checks),
        "up_to_date_at_run_start": seen.get((done.pr, pull.head_sha))}


def _conflict_record(run: _Run, loser: str | None, prepared: dict, order: list[str], opened: dict, pulls: dict,
                     by_pr: dict, shared: str, winners: int) -> dict:
    if loser is None:
        return {"passed": False, "error": "no pull request conflicted: both that add the shared file merged"}
    done, served = opened[loser], by_pr.get(opened[loser].pr)
    ticket = prepared[loser][0]
    state = run.read(ticket, done.pr)
    behind = [label for label in order[order.index(loser) + 1:]]
    merged_behind = [opened[label].pr for label in behind if pulls[label].state == "merged"]
    counted = [d.get("counted") for kind, d in run.events(ticket) if kind == "attempt.counted"]
    row = {
        "pr": done.pr, "ticket": ticket, "shared_file": shared, "line_result": served.result if served else None,
        "line_detail": served.detail if served else None,
        "waited_minutes": served.waited_minutes if served else None,
        "ticket_ready": state["ready"], "ticket_parked": state["parked"], "ticket_open": state["open"],
        "last_comment": state["comment"][:300], "last_comment_names_the_file": shared in state["comment"],
        "attempt_counted": counted, "behind": [opened[label].pr for label in behind], "merged_behind": merged_behind,
        "in_line": state["in_line"]}
    row["passed"] = bool(
        state["ready"] and not state["parked"] and state["open"] and row["last_comment_names_the_file"]
        and counted == [False] and behind and len(merged_behind) == len(behind) and not state["in_line"]
        and winners == 4)
    return row


def _ending(run: _Run, made: tuple[int, str, Opened], expected: str, judge, *, wanted_event: str | None = None,
            unwanted_event: str | None = None, counted: bool | None = None) -> dict:
    """Follow one pull request of row 4 and read its ticket back from GitHub and the store."""

    ticket, _, opened = made
    done = run.follow(opened.pr)
    state = run.read(ticket, opened.pr)
    events = run.events(ticket)
    kinds = list(dict.fromkeys(kind for kind, _ in events))
    ok = done.outcome == expected and bool(judge(state))
    if wanted_event:
        ok = ok and wanted_event in kinds
    if unwanted_event:
        ok = ok and unwanted_event not in kinds
    if counted is not None:
        ok = ok and [d.get("counted") for kind, d in events if kind == "attempt.counted"] == [counted]
    return {"pr": opened.pr, "ticket": ticket, "follow": done.outcome, "detail": done.detail, **state,
            "store": kinds, "passed": ok}


# --- the run a row works in -------------------------------------------------------------------------------


class _Run:
    """The parts of a row (trial, hub, store, clock, sleep, plan) with the steps most rows share."""

    def __init__(self, trial: Trial, hub: PullHub, store: Store, clock: Callable[[], datetime],
                 sleep: Callable[[float], None], plan: Plan | None, row: str, live: bool = False):
        self.trial, self.hub, self.store, self.clock, self.sleep = trial, hub, store, clock, sleep
        self.live = live  # the row runs its workflows on GitHub (`--live`), the harness only waits and reads
        self.plan = plan or Plan()
        self.settings = self.plan.settings
        self.repo, self.base = trial.repo, trial.base
        self.owner = (self.plan.owner or self.repo.split("/")[0]).lower()
        self.tag = f"{clock():%m%d%H%M%S}{row}"
        self._git_lock = threading.Lock()

    def say(self, text: str) -> None:
        print(f"[{self.clock():%H:%M:%S}] {text}", flush=True)

    def git(self, args: list[str]) -> str:
        with self._git_lock:  # the pull requests are opened at once; git in the one work tree one at a time
            return self.trial.git(args)

    # waiting

    def wait(self, what: str, probe: Callable[[], object], minutes: float | None = None):
        """Poll `probe` every `poll_seconds` until it answers something; `TrialTimeout` after `minutes`."""

        end, failed, polls = self.clock() + timedelta(minutes=minutes or self.plan.wait_minutes), 0, 0
        while True:
            polls += 1
            if polls % 10 == 1:
                self.say(f"  waiting for {what}")
            try:
                found, failed = probe(), 0
            except GithubError as e:
                failed += 1
                if failed > 3:
                    raise
                self.say(f"  {what}: gh failed ({str(e)[:80]}); again")
                found = None
            if found:
                return found
            if self.clock() >= end:
                raise TrialTimeout(f"{what}: not within {minutes or self.plan.wait_minutes:g} minutes")
            self.sleep(self.settings.poll_seconds)

    def required_states(self, sha: str) -> dict[str, str]:
        found = {c.name: c.state for c in self.hub.checks(self.repo, sha)}
        return {name: found.get(name, "missing") for name in REQUIRED}

    def ended_red(self, pr: int) -> dict | None:
        """The required checks of a pull request's head once all ended and one failed, else None."""

        states = self.required_states(self.hub.pull(self.repo, pr).head_sha)
        ended = not {PENDING, "missing"} & set(states.values())
        return states if ended and FAILED in states.values() else None

    def land(self, pr: int) -> Pull:
        """Merge `pr` once its required checks are green on its head."""

        def probe() -> Pull | None:
            pull = self.hub.pull(self.repo, pr)
            if pull is None:
                raise TrialError(f"#{pr} is gone")
            if pull.state != "open":
                return pull
            states = self.required_states(pull.head_sha)
            if FAILED in states.values():
                raise TrialError(f"#{pr} is red: {states}")
            if set(states.values()) == {PASSED}:
                if pull.behind:
                    if not self.hub.update_branch(self.repo, pr, pull.head_sha):
                        raise TrialError(f"#{pr} conflicts with {self.base}")
                    return None
                self.hub.merge(self.repo, pr, pull.head_sha, self.settings.merge_method)
                again = self.hub.pull(self.repo, pr)
                return again if again is not None and again.state != "open" else None
            return None

        pull = self.wait(f"#{pr} green and merged", probe)
        if pull.state != "merged":
            raise TrialError(f"#{pr} ended {pull.state}, not merged")
        self.say(f"#{pr} merged at {(pull.merge_sha or '')[:12]}")
        return pull

    def ended_run(self, sha: str, attempt: int = 1):
        """The run of the `tests` workflow on the base at `sha`, at `attempt` or later, once it ended."""

        if attempt == 1:
            self.trial.dispatch_tests()

        def probe():
            for found in self.hub.branch_runs(self.repo, self.base):
                if found.sha == sha and found.attempt >= attempt and found.jobs and PENDING not in found.jobs.values():
                    return found
            return None

        found = self.wait(f"the tests run on {self.base} at {sha[:12]} (attempt {attempt})", probe)
        self.say(f"run {found.run_id} attempt {found.attempt} ended: {found.jobs}")
        return found

    # tickets and pull requests

    def prepare(self, label: str, files: dict[str, str | None], body: str = BODY) -> tuple[int, str]:
        ticket = self.trial.make_ticket(f"{TITLE} (trial {self.tag} {label})", body)
        branch = f"trial-{self.tag}-{label}"
        self.trial.make_branch(branch, files)
        self.say(f"{label}: ticket #{ticket}, branch {branch}")
        return ticket, branch

    def open_pull(self, ticket: int, branch: str, rows: str) -> Opened:
        return open_pr(self.hub, self.store, self.repo, ticket, acceptance=rows, branch=branch, base=self.base,
                       git=self.git, settings=self.settings, now=self.clock())

    def open_all(self, prepared: dict[str, tuple[int, str]], rows: str) -> dict[str, Opened]:
        """`pr open` each of them at once (the git calls one at a time); label -> what was opened."""

        workers = max(1, min(self.plan.open_workers, len(prepared)))
        if workers == 1:
            found = {label: self.open_pull(ticket, branch, rows) for label, (ticket, branch) in prepared.items()}
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {label: pool.submit(self.open_pull, ticket, branch, rows)
                           for label, (ticket, branch) in prepared.items()}
                found = {label: future.result() for label, future in futures.items()}
        for label, done in found.items():
            self.say(f"{label}: #{done.pr} opened at {done.head[:12]}, ticket parked: {done.park}")
        return found

    def pull_request(self, label: str, files: dict[str, str | None], *, body: str = BODY,
                     rows: str = ROWS) -> tuple[int, str, Opened]:
        """A ticket, its branch and its pull request opened by `pr open`: (ticket, branch, opened)."""

        ticket, branch = self.prepare(label, files, body)
        return ticket, branch, self.open_all({label: (ticket, branch)}, rows)[label]

    def ready_at(self, pr: int) -> datetime:
        ready = marks.last_ready(marks.of_layer(self.hub.pull_comments(self.repo, pr), self.owner))
        if ready is None:
            raise TrialError(f"#{pr} has no ready mark")
        return ready.at

    def follow(self, pr: int) -> FollowReport:
        done = follow(self.hub, self.store, self.repo, pr, base=self.base, settings=self.settings, now=self.clock(),
                      owner=self.owner)
        self.say(f"follow #{pr}: {done.outcome} {done.detail}".rstrip())
        return done

    def read(self, ticket: int, pr: int | None = None) -> dict:
        """A ticket (and its pull request) as GitHub holds them now."""

        issue = self.hub.issue(self.repo, ticket)
        parsed = tickets.parse(issue.body, title=issue.title, labels=issue.labels)
        said = self.hub.comments(self.repo, ticket)
        text = "\n".join(line for line in (said[-1].body if said else "").splitlines() if not line.startswith("<!--"))
        found = {"open": issue.open, "state_reason": issue.state_reason, "labels": sorted(issue.labels),
                 "ready": parsed.ready, "parked": parsed.parked or bool(parsed.park_lines),
                 "needs_triage": TRIAGE_LABEL in parsed.labels, "comment": text.strip(), "in_line": False}
        if pr is not None:
            pull = self.hub.pull(self.repo, pr)
            held = marks.ready_since(marks.of_layer(self.hub.pull_comments(self.repo, pr), self.owner))
            found |= {"pr_state": pull.state, "in_line": held is not None}
        return found

    def events(self, ticket: int) -> list[tuple[str, dict]]:
        rows = self.store.query("SELECT kind, detail FROM events WHERE ticket = ? AND repo = ? ORDER BY at, id",
                                (ticket, self.repo)).rows
        return [(kind, json.loads(detail) if isinstance(detail, str) else (detail or {})) for kind, detail in rows]

    def adopt(self, report) -> None:
        """The tickets a main check opened are the trial's: cleanup closes them if the row ends before green does."""

        for number in (*report.opened, *report.brain):
            self.trial.adopt(number)

    def red_tickets_of(self, ids: frozenset[str], *, settle: bool = False) -> list[int]:
        """The open red-on-main tickets that list exactly `ids`. The label list fills eventually, so the default
        waits (`Plan.list_minutes`) until a ticket appears; past that bound it returns the list as last read (so
        a check that opened no ticket records `[]`). `settle=True` is for what must NOT appear: wait
        `SETTLE_SECONDS`, read the list once and return it as read."""

        if settle:
            self.sleep(SETTLE_SECONDS)
            return self._red_tickets_read(ids)
        last: list[int] = []

        def read() -> list[int]:
            nonlocal last
            last = self._red_tickets_read(ids)
            return last

        try:
            return self.wait(f"a red-on-main ticket of {len(ids)} failing test(s)", read,
                             minutes=self.plan.list_minutes)
        except TrialTimeout:
            return last

    def _red_tickets_read(self, ids: frozenset[str]) -> list[int]:
        found = []
        for number in self.hub.issues_with_label(self.repo, "priority"):
            issue = self.hub.issue(self.repo, number)
            if issue is not None and issue.open and red_main.ids_of(issue.body) == ids:
                found.append(number)
        return sorted(found)

    @contextmanager
    def without(self, workflow: str):
        """`workflow` disabled for the block (True when it was), enabled again after it."""

        try:
            self.trial.disable_workflow(workflow)
            off = True
            self.say(f"workflow {workflow} disabled")
        except TrialError as e:
            off = False
            self.say(f"workflow {workflow} could not be disabled ({str(e)[:100]}): its events may race the trial")
        try:
            yield off
        finally:
            if off:
                self.trial.enable_workflow(workflow)
                self.say(f"workflow {workflow} enabled")


# --- the real adapter ---------------------------------------------------------------------------------------


_ORIGIN = re.compile(r"(?:https?://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
                     r"(?P<owner>[A-Za-z0-9-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?")


def origin_repo(url: str) -> str | None:
    """The OWNER/NAME a GitHub remote url names (with or without `.git`); None for any other url."""

    found = _ORIGIN.fullmatch(url.strip())
    return f"{found['owner']}/{found['name']}" if found else None


class RealTrial:
    """The trial over a clone (`work_tree`, its `origin` the repo) and the owner's `gh`."""

    def __init__(self, repo: str, base: str, work_tree: Path, hub: PullHub, *, gh: Gh = run_gh,
                 lock_root: Path | None = None, identity: tuple[str, str] | None = None,
                 allow_default_base: bool = False, dispatch_base_runs: bool = False):
        self.repo, self.base, self.work_tree, self.hub, self.gh = repo, base, Path(work_tree), hub, gh
        self.git = merge_git.work_tree(self.work_tree)
        self.lock_root = lock_root
        self.identity = identity
        self.allow_default_base = allow_default_base
        self.dispatch_base_runs = dispatch_base_runs
        self.default_branch: str | None = None  # what the last preflight read from the repo
        self.tickets: list[int] = []  # every ticket the trial made or adopted
        self.branches: set[str] = set()  # every branch it made
        self._workflows_off: set[str] = set()
        self._actions_off = False

    def preflight(self) -> dict:
        """What the trial stands on; a repo or clone it cannot run on is `TrialError`, said before any row."""

        origin = self.git(["remote", "get-url", "origin"]).strip()
        if (origin_repo(origin) or "").lower() != self.repo.lower():
            raise TrialError(f"the work tree's origin {origin} is not exactly {self.repo}")
        # untracked files are not the trial's concern; a change to a tracked file is
        if self.git(["status", "--porcelain", "--untracked-files=no"]).strip():
            raise TrialError(f"the work tree {self.work_tree} has changes to tracked files: use a clean clone")
        info = self._json(["api", f"repos/{self.repo}"])
        default = info.get("default_branch")
        self.default_branch = default
        if default == self.base and not self.allow_default_base:
            raise TrialError(f"{self.base} is the default branch of {self.repo} and row 5 puts a red test on the "
                             "base: use a throwaway repo or a trial branch, or give --allow-default-base")
        try:
            branch = self._json(["api", f"repos/{self.repo}/branches/{self.base}"])
        except TrialError as e:
            raise TrialError(f"the base branch {self.base} is not in {self.repo}: {e}") from None
        try:
            self._json(["api", f"repos/{self.repo}/actions/workflows/{WORKFLOW_FILE}"])
        except TrialError as e:
            raise TrialError(f"{self.repo} has no `{WORKFLOW_FILE}` workflow: {e}") from None
        try:
            rule = self._json(["api", f"repos/{self.repo}/branches/{self.base}/protection"])
        except TrialError as e:
            raise TrialError(f"the base {self.base} has no readable branch protection: {e}") from None
        checks = rule.get("required_status_checks") or {}
        required = sorted({c.get("context") for c in checks.get("checks") or [] if isinstance(c, dict)}
                          | set(checks.get("contexts") or []))
        if checks.get("strict") is not True:
            raise TrialError(f"the protection of {self.base} does not require branches to be up to date (strict)")
        if required != sorted(REQUIRED):
            raise TrialError(f"the protection of {self.base} requires {required}, not exactly {sorted(REQUIRED)}")
        return {"default_branch": default, "private": info.get("private"), "base_is_default": default == self.base,
                "base_protected": branch.get("protected"), "strict": True, "required": required}

    def _json(self, args: list[str]) -> dict:
        try:
            value = json.loads(self.gh(args))
        except GithubError as e:
            raise TrialError(f"gh {' '.join(args[:2])}: {e}") from None
        except json.JSONDecodeError as e:
            raise TrialError(f"gh {' '.join(args[:2])}: not JSON ({e})") from None
        if not isinstance(value, dict):
            raise TrialError(f"gh {' '.join(args[:2])}: not an object")
        return value

    def make_ticket(self, title: str, body: str) -> int:
        number = self.hub.create_issue(self.repo, title, body, [READY_LABEL])
        self.adopt(number)
        return number

    def adopt(self, ticket: int) -> None:
        if ticket not in self.tickets:
            self.tickets.append(ticket)

    def make_branch(self, name: str, files: dict[str, str | None]) -> str:
        git, written = self.git, []
        self.branches.add(name)
        try:
            git(["fetch", "--quiet", "origin", self.base])
            git(["checkout", "--quiet", "-B", name, f"origin/{self.base}"])
            for path, text in files.items():
                if text is None:
                    git(["rm", "--quiet", "--ignore-unmatch", "--", path])
                    continue
                target = self.work_tree / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, "utf-8", newline="\n")
                written.append(path)
                git(["add", "--", path])
            name_email = self.identity or ()
            who = ["-c", f"user.name={name_email[0]}", "-c", f"user.email={name_email[1]}"] if name_email else []
            git([*who, "commit", "--quiet", "-m", f"trial: {name}"])
            git(["push", "--quiet", "origin", f"{name}:refs/heads/{name}"])
            return git(["rev-parse", "HEAD"]).strip()
        except BaseException:
            self._reset(written)
            raise

    def _reset(self, written: list[str]) -> None:
        """After a branch that half failed: the work tree back at the base, the files this branch wrote gone."""

        try:
            self.git(["checkout", "--quiet", "--force", "--detach", f"origin/{self.base}"])
            for path in written:
                if not self.git(["ls-files", "--", path]).strip():  # untracked: this script wrote it
                    (self.work_tree / path).unlink(missing_ok=True)
        except (merge_git.GitError, OSError) as e:  # the failure being raised is the one that matters
            print(f"could not reset the work tree to {self.base}: {e}", flush=True)

    def close_pull(self, pr: int) -> None:
        self.gh(["pr", "close", str(pr), "--repo", self.repo])

    def comment_as_person(self, pr: int, text: str) -> None:
        self.gh(["pr", "comment", str(pr), "--repo", self.repo, "--body", text])

    def disable_workflow(self, file: str) -> None:
        try:
            self.gh(["workflow", "disable", file, "--repo", self.repo])
        except GithubError as e:
            raise TrialError(f"workflow {file}: {e}") from None
        self._workflows_off.add(file)

    def enable_workflow(self, file: str) -> None:
        self.gh(["workflow", "enable", file, "--repo", self.repo])
        self._workflows_off.discard(file)

    def dispatch_tests(self) -> None:
        if self.dispatch_base_runs:
            self.gh(["workflow", "run", "tests.yml", "--repo", self.repo, "--ref", self.base,
                     "-f", "slow_and_live=false"])

    def dispatch_workflow(self, file: str) -> None:
        self.gh(["workflow", "run", file, "--repo", self.repo, "--ref", self.base])

    def workflow_runs(self, file: str) -> list[dict]:
        # GithubError is left to propagate: `_Run.wait` reads a failed listing again, a few times
        text = self.gh(["api", f"repos/{self.repo}/actions/workflows/{file}/runs?per_page=30"])
        try:
            listed = json.loads(text)
        except json.JSONDecodeError as e:
            raise GithubError("gh_bad_output", f"{self.repo}: the runs of {file} are not JSON ({e})") from None
        if not isinstance(listed, dict):
            raise GithubError("gh_bad_output", f"{self.repo}: the runs of {file} are not an object")
        return list(listed.get("workflow_runs") or [])

    def rate_remaining(self) -> int:
        return self._json(["api", "rate_limit"])["resources"]["core"]["remaining"]

    def set_actions(self, on: bool) -> None:
        self.gh(["api", "-X", "PUT", f"repos/{self.repo}/actions/permissions", "-F", f"enabled={str(on).lower()}"])
        self._actions_off = not on

    def cleanup(self) -> dict:
        """Every trial ticket still open: `ready-for-agent` off first (no agent takes it), a comment, closed
        `not_planned`; then every pull request of a trial branch still open, closed."""

        done: dict = {"tickets": [], "pulls": [], "errors": []}
        for number in self.tickets:
            try:
                issue = self.hub.issue(self.repo, number)
                if issue is None or not issue.open:
                    continue
                if READY_LABEL in issue.labels:
                    self.hub.remove_label(self.repo, number, READY_LABEL)
                self.hub.comment(self.repo, number, "Closed by the merge trial's cleanup: a ticket made for a trial "
                                 "run, not planned work.")
                self.hub.close_issue(self.repo, number, "not_planned")
                done["tickets"].append(number)
            except Exception as e:  # noqa: BLE001 - the others are still closed
                done["errors"].append(f"ticket #{number}: {e}"[:200])
        try:
            for pull in self.hub.open_pulls(self.repo, self.base):
                if pull.branch in self.branches and (pull.head_repo or "").lower() == self.repo.lower():
                    self.hub.close_pull(self.repo, pull.number)
                    done["pulls"].append(pull.number)
        except Exception as e:  # noqa: BLE001
            done["errors"].append(f"pull requests: {e}"[:200])
        return done

    def restore(self) -> None:
        """Turn on again Actions and every workflow this trial turned off; one that fails does not stop the others."""

        errors = []
        if self._actions_off:
            try:
                self.set_actions(True)
            except Exception as e:  # noqa: BLE001
                errors.append(f"Actions: {e}")
        for file in sorted(self._workflows_off):
            try:
                self.enable_workflow(file)
            except Exception as e:  # noqa: BLE001
                errors.append(f"workflow {file}: {e}")
        if errors:
            raise TrialError("still off: " + "; ".join(errors)[:600])


# --- the script --------------------------------------------------------------------------------------------


def _summary(row: int, result: dict) -> str:
    if "error" in result and not result.get("prs"):
        return str(result["error"])[:100]
    if row == 2:
        waits = " ".join(f"{p['waited_minutes']:g}" for p in result["prs"] if p["waited_minutes"] is not None)
        runs = ",".join(str(p["runs_on_merged_head"]) for p in result["prs"])
        return (f"merged {result['merged']}/{result['tickets']} in order={result['in_order']}, waited min {waits}, "
                f"runs on merged head {runs}, ready span {result['ready_span_s']:.0f} s")
    if row == 3:
        return (f"#{result['pr']} {result['line_result']}/{result['line_detail']}, "
                f"ticket ready={result['ticket_ready']}, counted={result['attempt_counted']}, "
                f"behind merged {len(result['merged_behind'])}/{len(result['behind'])}")
    if row == 4:
        return ", ".join(f"{end}={'ok' if result[end]['passed'] else 'NO'}" for end in
                         ("merged", "after_merge", "red", "closed_unmerged", "comment") if end in result)
    if row == 5:
        return (f"tickets {result['first_check']['red_tickets']}, shape violations "
                f"{result['first_check'].get('shape_violations')}, second check opened "
                f"{result['second_check']['opened']}, closed {result['third_check']['closed']}")
    return ", ".join(f"{half}: closed={not result[half]['ticket_open_after']} actions={result[half].get('actions')}"
                     for half in ("workflow_off", "actions_off") if half in result)


def _real_world(args: argparse.Namespace):
    hub = GhPullHub()
    identity = None
    if args.identity:
        name, _, email = args.identity.partition("<")
        identity = (name.strip(), email.strip().rstrip(">"))
    trial = RealTrial(args.repo, args.base, Path(args.work_tree), hub,
                      lock_root=Path(args.lock_root) if args.lock_root else None, identity=identity,
                      allow_default_base=args.allow_default_base, dispatch_base_runs=args.dispatch_base_runs)
    return trial, hub, lambda: datetime.now(UTC), time.sleep


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="merge_trial.py", description="the live trial of the merge flow (#400 rows 2-6)")
    p.add_argument("--repo", required=True, help="OWNER/NAME of the repo the trial runs on")
    p.add_argument("--base", required=True, help="the protected branch the pull requests go into")
    p.add_argument("--work-tree", required=True, metavar="PATH", help="a clean clone of the repo, free to use")
    p.add_argument("--rows", default=",".join(map(str, ROWS_ALL)), help="the rows to run (default: 2,3,4,5,6)")
    p.add_argument("--out", default=None, metavar="FILE", help="the JSON of the numbers (default: merge-trial.json)")
    p.add_argument("--owner", default=None, help="the login the layer's comments have (default: the repo's owner)")
    p.add_argument("--store", default=None, metavar="PATH",
                   help="the trial's own event store, outside any git work tree (default: a new temp folder)")
    p.add_argument("--keep", action="store_true", help="leave the trial's tickets and pull requests open at the end")
    p.add_argument("--allow-default-base", action="store_true",
                   help="allow --base to be the repo's default branch (row 5 puts a red test on it)")
    p.add_argument("--dispatch-base-runs", action="store_true",
                   help="ask for each base run of `tests` by workflow_dispatch (a trial branch `tests.yml` "
                        "runs no push on)")
    p.add_argument("--lock-root", default=None, metavar="PATH", help="the merge line's lock folder")
    p.add_argument("--identity", default=None, metavar='"Name <email>"', help="author of the trial's commits")
    p.add_argument("--line-minutes", type=float, default=90, help="the bound of the line's run (default: 90)")
    p.add_argument("--check-minutes", type=float, default=30, help="the line's wait for one head's checks (30)")
    p.add_argument("--wait-minutes", type=float, default=45, help="the bound of every other wait (45)")
    p.add_argument("--live", action="store_true",
                   help="rows 4, 5 and 6 run on the repo's own workflows (the live forms, "
                        "tests/merge_trial_live_r*.py): the trial only waits and reads; the repo is a throwaway one; "
                        "row 5 live needs --allow-default-base, its base being the default branch where the main "
                        "check runs")
    args = p.parse_args(argv)
    if not schema.is_repo(args.repo):
        p.error(f"--repo {args.repo!r} is no OWNER/NAME")
    try:
        args.row_list = sorted({int(n) for n in args.rows.split(",") if n.strip()})
    except ValueError:
        p.error(f"--rows {args.rows!r}: row numbers separated by commas")
    if not args.row_list or not set(args.row_list) <= set(ROWS_ALL):
        p.error(f"--rows {args.rows!r}: rows from 2 to 6")
    return args


def main(argv: list[str] | None = None, *, world=_real_world) -> int:
    """Run the rows asked for; `world(args)` gives (trial, hub, clock, sleep), the tests' fixture GitHub too."""

    args = parse_args(argv)
    trial, hub, clock, sleep = world(args)
    settings = MergeSettings(line_minutes=args.line_minutes, lock_stale_minutes=args.line_minutes + 90,
                             check_minutes=args.check_minutes)
    plan = Plan(settings, args.wait_minutes, args.owner)
    store_path = args.store or tempfile.mkdtemp(prefix="orch-it-merge-trial-")
    store = Store.open(store_path)
    out = Path(args.out or "merge-trial.json")
    report: dict = {"repo": args.repo, "base": args.base, "started": clock().isoformat(), "plan": asdict(plan),
                    "store": str(store_path), "rows": {}}
    wanted, ready = args.row_list, True
    try:
        try:
            report["preflight"] = trial.preflight()
        except (TrialError, GithubError, merge_git.GitError) as e:
            report["error"] = f"preflight: {e}"
            print(f"cannot run here: {e}", flush=True)
            wanted, ready = [], False
        print(f"trial on {args.repo} into {args.base}: {report.get('preflight')}", flush=True)
        if 2 in wanted or 3 in wanted:
            _attempt(report, (2, 3), lambda: dict(zip((2, 3), line_rows(trial, hub, store, clock, sleep, plan))))
        for number in (4, 5, 6):
            if number in wanted:  # a live row's function is looked up inside the attempt: a missing module is its error
                _attempt(report, (number,), lambda n=number: {n: _row_function(n, args.live)(
                    trial, hub, store, clock, sleep, plan)})
    finally:
        if not args.keep:  # before the workflows are on again: closing a trial ticket or pull request starts none
            try:
                report["cleanup"] = trial.cleanup()
                print(f"cleanup: {report['cleanup']}", flush=True)
            except Exception as e:  # noqa: BLE001 - restore and the report still follow
                report["cleanup_error"] = f"{type(e).__name__}: {e}"
                print(f"CLEANUP FAILED: {e} - close the trial's tickets and pull requests by hand", flush=True)
        try:
            trial.restore()
        except Exception as e:  # noqa: BLE001 - say what stayed off, and still write what was measured
            report["restore_error"] = f"{type(e).__name__}: {e}"
            print(f"RESTORE FAILED: {e} - turn Actions and the disabled workflows on by hand", flush=True)
        try:  # the cost of the run: one call, shown in the report so a live run says what it used
            report["rate_limit"] = trial.rate_remaining()
            print(f"gh rate limit remaining (core): {report['rate_limit']}", flush=True)
        except Exception as e:  # noqa: BLE001 - the report is written without it
            report["rate_limit"] = f"{type(e).__name__}: {e}"[:200]
        report["finished"] = clock().isoformat()
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", "utf-8")
        _table(report, wanted, out)
    clean = not ({"restore_error", "cleanup_error"} & report.keys()) and not (report.get("cleanup") or {}).get("errors")
    return 0 if ready and clean and all(report["rows"].get(str(n), {}).get("passed") for n in wanted) else 1


def _row_function(number: int, live: bool) -> Callable:
    return live_row_function(number) if live else ROW_FUNCTIONS[number]


def _attempt(report: dict, numbers: tuple[int, ...], make: Callable[[], dict]) -> None:
    try:
        found = make()
    except Exception as e:  # noqa: BLE001 - a row that breaks is a result; the next rows still run
        traceback.print_exc()
        found = {n: {"passed": False, "error": f"{type(e).__name__}: {e}"[:400]} for n in numbers}
    for number, result in found.items():
        report["rows"][str(number)] = result


def _table(report: dict, wanted: list[int], out: Path) -> None:
    print("\nrow  result  numbers")
    for number in wanted:
        result = report["rows"].get(str(number))
        if result is None:
            print(f"{number:<4} not run")
            continue
        try:
            said = _summary(number, result)
        except (KeyError, TypeError):
            said = str(result.get("error", "no numbers"))[:100]
        print(f"{number:<4} {'PASS' if result.get('passed') else 'FAIL':<7} {said}")
    print(f"\nnumbers written to {out}")


if __name__ == "__main__":
    sys.exit(main())
