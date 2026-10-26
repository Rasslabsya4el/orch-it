"""The ticket follows its pull request (#400 decision 3): `follow` reads one pull request and its
ticket (the line `Refs #N` of its body) from GitHub alone and moves the ticket once, by the `pr` and
`close` rules of the pipeline model. It is called by the workflow `follow` on pull request, review,
comment and ticket events, by `reconcile`, by the merge line on its head and by the park reader on a
`pr_ended` park whose pull request ended. Calling it again changes nothing: a pull request it took
out of the line (auto-merge off, a left mark) is `idle` until `pr open` puts it back.

Merged (`close` rules): the ticket is closed with its acceptance rows (`closed`), or, when the ticket
has rows marked `after merge`, its park comes off and it goes back to ready for those rows only
(`after_merge`); with no acceptance comment for this pull request (`acceptance_missing`), a failed
row (`row_failed`), a body changed since the rows were checked (`ticket_changed`) or a person's
comment on the ticket after them (`unanswered`) it goes to the brain and stays open; a ticket
already closed is only recorded (`already_closed`).

Closed unmerged: a ticket parked on it goes to the brain (`closed_unmerged`).

Open and in the line (a ready mark, or auto-merge on), in this order: the ticket closed
(`withdrawn`: out of the line) or edited since its rows were checked (`edited`: out of the line,
back to ready to be planned again); a person's comment on the pull request after its ready mark
(`commented`) or on the ticket after its acceptance comment (`unanswered`): out of the line, the
ticket to the brain; a conflict (`conflict`): out of the line, the ticket back to ready with the
files, not counted; then the required checks of the head sha only (MERGE-12):

- one red: the failed tests' ids from the run's reports; every one also red on main (an open
  red-on-main ticket lists it) parks the ticket on that ticket, not counted (`red_on_main`);
  else the ticket goes back to ready with the ids, counted (`red`), or to the brain on the
  `red_in_a_row`th red in a row (`red_in_a_row`); a red with no readable report is no result;
- no result (cut, cancelled, no report): run once more (`rerun`), never an attempt; no result again
  on the next attempt goes to the brain (`no_result`);
- a check pending longer than `check_stuck_minutes` is cancelled (`stuck`), so it ends with no result;
- open past `pr_max_hours` from its ready mark: out of the line, to the brain (`timeout`);
- else `waiting` (a check pending or missing: no check is never green, MERGE-1), `behind` or `green`:
  nothing changes, the line brings it up to date and merges it.

"Out of the line" is auto-merge off and a left mark on the pull request; "to ready" is the park
off and `ready-for-agent` on; "to the brain" is the park off, `needs-triage` on and the ready labels
off; each with a comment carrying the follow mark of its outcome. A pull request with no ticket only
leaves the line. Events: the end of the `pr` step (run `pr:<repo>#<n>@<ready mark>`), the `close`
pair, the park's `wait` end with `ticket.unparked`, `ticket.closed`, `comment.written`,
`attempt.counted`, `pr.commented`, `pr.conflicted`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from orch_it.events.store import Store
from orch_it.merge import marks, record, red_main
from orch_it.merge.hub import FAILED, NO_RESULT, PENDING, Check, Pull, PullHub
from orch_it.merge.pr_open import refers_to
from orch_it.merge.settings import MergeSettings
from orch_it.testruns.github import DEFAULT_REQUIRED_JOBS
from orch_it.tickets import park
from orch_it.tickets.hub import Issue
from orch_it.tickets.model import PRIORITY_LABEL, READY_LABEL, parse
from orch_it.tickets.reader import HUMAN_LABEL, TRIAGE_LABEL

ROLE = "github"
TO_THE_BRAIN = ("acceptance_missing", "row_failed", "ticket_changed", "unanswered", "closed_unmerged", "commented",
                "timeout", "no_result", "red_in_a_row")
# how the park of a ticket ends, by what the follow did: (wait outcome, wait reason, `returned_as`)
_PARK_END = {"due": ("passed", None), "other_way": ("failed", "other_way"), "comment": ("stopped", "comment"),
             "late": ("no_result", "late")}


class FollowError(LookupError):
    """`code`: `pr_missing` (no such pull request), `bad_options` (the command given both a pull
    request and an event, or neither)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class FollowReport:
    pr: int
    ticket: int | None
    outcome: str
    detail: str = ""


def follow(hub: PullHub, store: Store, repo: str, number: int, *, base: str = "main", settings: MergeSettings,
           now: datetime, role: str = ROLE, required: tuple[str, ...] = tuple(DEFAULT_REQUIRED_JOBS),
           owner: str | None = None) -> FollowReport:
    """Move the ticket of pull request `number` as its pull request stands now; see the module text.
    `owner`: the login whose comment is the owner's (default: the repo's owner)."""

    pull = hub.pull(repo, number)
    if pull is None:
        raise FollowError("pr_missing", f"{repo}#{number} is no pull request")
    referred = refers_to(pull.body)
    issue = hub.issue(repo, referred) if referred else None
    if issue is not None and issue.is_pr:
        issue = None
    one = _Follow(hub, store, repo, pull, issue, settings, now, role, (owner or repo.split("/")[0]).lower(),
                  required)
    if pull.state == "merged":
        return one.merged()
    if pull.state == "closed":
        return one.closed()
    if pull.base != base:
        return one.report("idle", f"into {pull.base}, not {base}")
    return one.open()


class _Follow:
    def __init__(self, hub: PullHub, store: Store, repo: str, pull: Pull, issue: Issue | None,
                 settings: MergeSettings, now: datetime, role: str, owner: str, required: tuple[str, ...]):
        self.hub, self.store, self.repo, self.pull, self.issue = hub, store, repo, pull, issue
        self.settings, self.now, self.role, self.owner, self.required = settings, now, role, owner, required
        self.said = hub.pull_comments(repo, pull.number)
        started = marks.last_ready(marks.of_layer(self.said, owner))
        self.run = record.pr_run(repo, pull.number, started.at if started else None)

    @property
    def ticket(self) -> int | None:
        return self.issue.number if self.issue else None

    def report(self, outcome: str, detail: str = "") -> FollowReport:
        return FollowReport(self.pull.number, self.ticket, outcome, detail)

    # merged: the close rules

    def merged(self) -> FollowReport:
        merge = {"merge_sha": self.pull.merge_sha}
        if self.issue is None:
            self._end_pr("passed", "merged", merge)
            return self.report("merged_alone")
        if not self.issue.open:
            self._close_step("refused", "already_closed")
            self._end_pr("passed", "merged", merge)
            return self.report("already_closed")
        ticket = parse(self.issue.body, title=self.issue.title, labels=self.issue.labels)
        said = self.hub.comments(self.repo, self.issue.number)
        accepted = marks.acceptance(marks.of_layer(said, self.owner))
        why = None
        if accepted is None or accepted.pr != self.pull.number:
            why = "acceptance_missing", f"no acceptance comment for #{self.pull.number}"
        elif any(row.result == "fail" for row in accepted.rows):
            why = "row_failed", f"row {', '.join(r.id for r in accepted.rows if r.result == 'fail')} failed"
        elif accepted.body != marks.body_hash(self.issue.body):
            why = "ticket_changed", "the ticket was edited after its rows were checked"
        elif self._a_person_said(said, accepted.at):
            why = "unanswered", "a person's comment on the ticket after its acceptance is not answered"
        if why:
            self._to_brain(why[0], f"#{self.pull.number} merged, but the ticket is not closed: {why[1]}.", "other_way")
            self._close_step("failed", why[0])
            self._end_pr("passed", "merged", merge)
            return self.report(why[0], why[1])
        later = [row.id for row in ticket.acceptance or () if row.after_merge]
        sha = (self.pull.merge_sha or "")[:12]
        if later:
            rows = ", ".join(later)
            self._to_ready("after_merge", f"#{self.pull.number} merged at `{sha}`. Only row{'s' if len(later) > 1 else ''}"
                           f" {rows} {'are' if len(later) > 1 else 'is'} left: run on main, then close the ticket.",
                           "due")
            self._close_step("passed", None)
            self._end_pr("passed", "merged", merge)
            return self.report("after_merge", f"rows {rows}")
        table = "".join(f"| {row.id} | {row.result} | {marks._cell(row.evidence)} |\n" for row in accepted.rows)
        self.hub.comment(self.repo, self.issue.number,
                         f"{marks.follow_mark('closed')}Closed: #{self.pull.number} merged at `{sha}` with every "
                         f"acceptance row checked.\n\n| row | result | evidence |\n|---|---|---|\n{table}")
        parked = self._park_wait_run()
        park.take_off(self.hub, self.issue)
        self.hub.close_issue(self.repo, self.issue.number, "completed")
        passed = sum(row.result in ("pass", "na") for row in accepted.rows)
        events = [("comment.written", {"comment_kind": "closing"}),
                  ("ticket.closed", {"via": "close_command", "state_reason": "completed",
                                     "rows_total": len(accepted.rows), "rows_passed": passed,
                                     "comments_unanswered": 0})]
        self._write(None, None, events)
        if parked:
            self._write(parked, "wait", [("step.ended", {"outcome": "passed"})])
        self._close_step("passed", None)
        self._end_pr("passed", "merged", merge)
        return self.report("closed", f"rows {passed}/{len(accepted.rows)}")

    # closed unmerged

    def closed(self) -> FollowReport:
        self._end_pr("failed", "closed_unmerged")
        if self.issue is None or not self.issue.open or not self._parked_on_this():
            return self.report("closed_unmerged", "no ticket waits on it")
        self._to_brain("closed_unmerged", f"#{self.pull.number} was closed without a merge.", "other_way")
        return self.report("closed_unmerged")

    # open

    def open(self) -> FollowReport:
        ready = marks.ready_since(marks.of_layer(self.said, self.owner))
        if ready is None and self.pull.auto_merge is None:
            return self.report("idle", "not in the merge line")
        since = ready.at if ready else self.pull.created_at
        if self.issue is not None and not self.issue.open:
            self._leave("withdrawn", f"its ticket #{self.issue.number} was closed")
            self._end_pr("stopped", "withdrawn")
            return self.report("withdrawn")
        if self.issue is not None:
            said = self.hub.comments(self.repo, self.issue.number)
            accepted = marks.acceptance(marks.of_layer(said, self.owner))
            if accepted is not None and accepted.pr == self.pull.number:
                if accepted.body != marks.body_hash(self.issue.body):
                    self._leave("edited", f"its ticket #{self.issue.number} was edited after its rows were checked")
                    self._to_ready("edited", f"The ticket was edited while #{self.pull.number} waited: plan it "
                                   "again, then `pr open` puts the pull request back in the line.", "other_way")
                    self._end_pr("stopped", "withdrawn")
                    return self.report("edited")
                if self._a_person_said(said, accepted.at):
                    self._leave("unanswered", f"a person commented on its ticket #{self.issue.number}")
                    self._to_brain("unanswered", f"A person commented here while #{self.pull.number} waited to "
                                   "merge: it is out of the merge line until the brain answers.", "comment")
                    self._end_pr("stopped", "withdrawn")
                    return self.report("unanswered")
        people = [c for c in self.said if marks.is_person(c, self.owner) and c.at > since]
        if people:
            return self._commented(people[0])
        if self.pull.mergeable == "conflicting":
            return self._conflict()
        checks = {check.name: check for check in self.hub.checks(self.repo, self.pull.head_sha)}
        found = [checks.get(name) for name in self.required]
        if any(c is not None and c.state == FAILED for c in found):
            return self._red([c for c in found if c is not None and c.state == FAILED])
        if any(c is not None and c.state == NO_RESULT for c in found):
            return self._no_result([c for c in found if c is not None and c.state == NO_RESULT])
        bound = timedelta(minutes=self.settings.check_stuck_minutes)
        stuck = [c for c in found if c is not None and c.state == PENDING and c.started_at
                 and self.now - c.started_at > bound]
        if stuck:
            for run_id in dict.fromkeys(c.run_id for c in stuck if c.run_id):
                self.hub.cancel(self.repo, run_id)
            return self.report("stuck", ", ".join(c.name for c in stuck))
        if self.now - since > timedelta(hours=self.settings.pr_max_hours):
            self._leave("timeout", f"open more than {self.settings.pr_max_hours:g} hours in the line")
            self._to_brain("timeout", f"#{self.pull.number} has waited more than {self.settings.pr_max_hours:g} "
                           "hours in the merge line without merging.", "late")
            self._end_pr("no_result", "timeout")
            return self.report("timeout")
        if any(c is None or c.state == PENDING for c in found):
            return self.report("waiting", ", ".join(n for n, c in zip(self.required, found) if c is None or
                                                    c.state == PENDING))
        if self.pull.behind:
            return self.report("behind", f"{self.pull.behind} commits")
        return self.report("green")

    def _commented(self, first) -> FollowReport:
        self._leave("commented", f"{first.author} commented on it")
        role = "owner" if first.author.lower() == self.owner else ROLE
        self._write(None, None, [("pr.commented", {"pr": self.pull.number, "author_role": role, "answered": False})])
        if self.issue is not None and self.issue.open:
            self._to_brain("commented", f"{first.author} commented on #{self.pull.number}: it is out of the "
                           "merge line until the brain answers.", "comment")
        self._end_pr("stopped", "withdrawn")
        return self.report("commented", first.author)

    def _conflict(self) -> FollowReport:
        files = self.hub.conflict_files(self.repo, self.pull.base, self.pull.number)
        return_conflict(self, files)
        return self.report("conflict", ", ".join(files))

    def _red(self, failed: list[Check]) -> FollowReport:
        ids: set[str] = set()
        for run_id in dict.fromkeys(c.run_id for c in failed):
            got = self.hub.failing_ids(self.repo, run_id) if run_id else None
            if not got:  # red with no test named: no result, never an attempt (MERGE-5)
                return self._no_result(failed)
            ids |= set(got)
        listed = "\n".join(f"- `{test_id}`" for test_id in sorted(ids)[:100])
        if self.issue is None or not self.issue.open:
            self._leave("red", f"{', '.join(c.name for c in failed)} red")
            self._end_pr("failed", "red")
            return self.report("red", f"{len(ids)} tests")
        covering = sorted(n for n, red in self._red_on_main() if ids <= red)
        if covering:
            self._leave("red_on_main", f"red only on tests red on main (#{covering[0]})")
            self._park_on_red_main(covering[0], listed)
            self._attempt("red_on_main", False, 0)
            self._end_pr("failed", "red")
            return self.report("red_on_main", f"#{covering[0]}")
        reds = 0
        for mark in reversed(marks.follows(marks.of_layer(self.hub.comments(self.repo, self.issue.number),
                                                            self.owner))):
            if mark.outcome != "red":
                break
            reds += 1
        in_a_row = reds + 1
        self._leave("red", f"{', '.join(c.name for c in failed)} red on `{self.pull.head_sha[:12]}`")
        if in_a_row >= self.settings.red_in_a_row:
            self._to_brain("red_in_a_row", f"#{self.pull.number} is red for the {in_a_row}th time in a row:\n\n"
                           f"{listed}", "other_way")
            outcome = "red_in_a_row"
        else:
            self._to_ready("red", f"#{self.pull.number} is red at `{self.pull.head_sha[:12]}` on these tests:\n\n"
                           f"{listed}", "other_way")
            outcome = "red"
        self._attempt("red", True, in_a_row)
        self._end_pr("failed", "red")
        return self.report(outcome, f"{len(ids)} tests")

    def _no_result(self, checks: list[Check]) -> FollowReport:
        attempt = max(c.attempt for c in checks)
        if attempt >= 2:
            self._leave("no_result", f"no result twice on `{self.pull.head_sha[:12]}`")
            if self.issue is not None and self.issue.open:
                self._to_brain("no_result", f"The checks of #{self.pull.number} gave no result twice "
                               f"({', '.join(c.name for c in checks)}).", "late")
            self._attempt("no_result", False, 0)
            self._end_pr("no_result", None)
            return self.report("no_result")
        for run_id in dict.fromkeys(c.run_id for c in checks if c.run_id):
            self.hub.rerun(self.repo, run_id)
        self._attempt("no_result", False, 0)
        return self.report("rerun", ", ".join(c.name for c in checks))

    # moving the ticket

    def _red_on_main(self) -> list[tuple[int, frozenset[str]]]:
        found = []
        for number in self.hub.issues_with_label(self.repo, PRIORITY_LABEL):
            issue = self.hub.issue(self.repo, number)
            ids = red_main.ids_of(issue.body) if issue is not None and issue.open else None
            if ids:
                found.append((number, ids))
        return found

    def _park_on_red_main(self, red: int, listed: str) -> None:
        issue = self.hub.issue(self.repo, self.issue.number)
        self.hub.comment(self.repo, issue.number,
                         f"{marks.follow_mark('red_on_main')}#{self.pull.number} is red only on tests red on main, "
                         f"listed in #{red}: it waits for that ticket, not counted.\n\n{listed}")
        self._end_park(issue, "other_way")
        park.take_off(self.hub, issue)
        until = self.now.date() + timedelta(days=self.settings.red_main_park_days)
        park.park(self.hub, self.store, self.repo, issue.number, "ticket_closed", f"#{red}", until,
                  today=self.now.date(), role=self.role)

    def _to_ready(self, outcome: str, text: str, returned_as: str) -> None:
        self._move(outcome, text, returned_as, add=(READY_LABEL,), remove=(TRIAGE_LABEL,), kind="send_back")

    def _to_brain(self, outcome: str, text: str, returned_as: str) -> None:
        self._move(outcome, f"To the brain: {text}", returned_as, add=(TRIAGE_LABEL,),
                   remove=(READY_LABEL, HUMAN_LABEL), kind="machine")

    def _move(self, outcome: str, text: str, returned_as: str, *, add, remove, kind: str) -> None:
        issue = self.hub.issue(self.repo, self.issue.number)
        if issue is None or not issue.open:
            return
        self.hub.comment(self.repo, issue.number, f"{marks.follow_mark(outcome)}{text}")
        self._end_park(issue, returned_as)
        park.take_off(self.hub, issue, add=add, remove=remove)
        self._write(None, None, [("comment.written", {"comment_kind": kind})])

    def _end_park(self, issue: Issue, returned_as: str) -> None:
        """End the `wait` of the ticket's park, if it holds one, with `ticket.unparked`."""

        held = parse(issue.body, labels=issue.labels)
        if not held.parked and not held.park_lines:
            return
        found = park.held_park(held.park_lines)
        run = self._park_wait_run()
        outcome, reason = _PARK_END[returned_as]
        ended = {"outcome": outcome, **({"reason": reason} if reason else {})}
        self._write(run, "wait", [("step.ended", ended)])
        self._write(run, None, [("ticket.unparked", {
            "by": "event", "returned_as": returned_as, "on_ref": found.ref if found else None,
            "late_minutes": park.late_minutes(found, self.now) if found else None})])

    def _park_wait_run(self) -> str | None:
        issue = self.issue
        held = parse(issue.body, labels=issue.labels) if issue else None
        if held is None or (not held.parked and not held.park_lines):
            return None
        return park.park_run(self.repo, issue.number, self.hub.parked_at(self.repo, issue.number))

    def _parked_on_this(self) -> bool:
        found = park.held_park(parse(self.issue.body).park_lines)
        ref = found.ticket if found is not None and found.kind == "pr_ended" else None
        return ref is not None and ref.names(self.repo, self.pull.number, self.repo)

    def _leave(self, reason: str, text: str) -> None:
        """Take the pull request out of the merge line: auto-merge off, then the left mark."""

        self.hub.set_auto_merge(self.repo, self.pull.number, None)
        self.hub.comment(self.repo, self.pull.number, marks.left_comment(reason, text))

    def _a_person_said(self, said, after: datetime) -> bool:
        return any(marks.is_person(c, self.owner) and c.at > after for c in said)

    # the store

    def _write(self, run: str | None, step: str | None, events: list[tuple[str, dict]]) -> None:
        record.write(self.store, self.repo, self.ticket, self.role, run, step, events)

    def _attempt(self, reason: str, counted: bool, in_a_row: int) -> None:
        self._write(None, None, [("attempt.counted", {"reason": reason, "counted": counted, "in_a_row": in_a_row})])

    def _end_pr(self, outcome: str, reason: str | None, detail: dict | None = None) -> None:
        ended = {"outcome": outcome, **({"reason": reason} if reason else {}), **(detail or {})}
        self._write(self.run, "pr", [("step.ended", ended)])

    def _close_step(self, outcome: str, reason: str | None) -> None:
        run = f"close:{self.repo}#{self.ticket}@{self.pull.number}"
        ended = {"outcome": outcome, **({"reason": reason} if reason else {})}
        self._write(run, "close", [("step.started", {"ticket": self.ticket, "pr": self.pull.number}),
                                   ("step.ended", ended)])


def follow_conflict(hub: PullHub, store: Store, repo: str, number: int, files: list[str], *,
                    settings: MergeSettings, now: datetime, role: str = ROLE, owner: str | None = None,
                    other_pr: int | None = None) -> FollowReport:
    """The conflict rule for a pull request the merge line could not bring up to date: out of the
    line, its ticket back to ready with the files, not counted."""

    pull = hub.pull(repo, number)
    if pull is None:
        raise FollowError("pr_missing", f"{repo}#{number} is no pull request")
    referred = refers_to(pull.body)
    issue = hub.issue(repo, referred) if referred else None
    one = _Follow(hub, store, repo, pull, issue if issue is not None and not issue.is_pr else None, settings, now, role,
                  (owner or repo.split("/")[0]).lower(), tuple(DEFAULT_REQUIRED_JOBS))
    return_conflict(one, files, other_pr)
    return one.report("conflict", ", ".join(files))


def pr_of_event(event: dict, hub: PullHub, repo: str) -> tuple[int | None, str]:
    """The pull request a GitHub event of the `follow` workflow is about, and why when none: a
    pull request, review or review comment event names it; a comment on a pull request is about
    it; a comment on, edit of or close of a ticket is about the pull request its `pr_ended` park
    names."""

    pull = event.get("pull_request")
    if isinstance(pull, dict) and isinstance(pull.get("number"), int):
        return pull["number"], ""
    issue = event.get("issue")
    if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
        return None, "no pull request or issue in the event"
    if isinstance(issue.get("pull_request"), dict):
        return issue["number"], ""
    found = hub.issue(repo, issue["number"])
    held = park.held_park(parse(found.body).park_lines) if found is not None else None
    ref = held.ticket if held is not None and held.kind == "pr_ended" else None
    if ref is None or (ref.repo and ref.repo.lower() != repo.lower()):
        return None, f"#{issue['number']} waits on no pull request of {repo}"
    return ref.number, ""


def return_conflict(one: _Follow, files: list[str], other_pr: int | None = None) -> None:
    """A conflict: out of the line, the ticket back to ready with the files, not counted (the line's
    head conflicts the same way)."""

    shown = "\n".join(f"- `{name}`" for name in files[:200]) or "- (GitHub names no file)"
    one._leave("conflict", f"it conflicts with {one.pull.base} in {len(files)} files")
    if one.issue is not None and one.issue.open:
        one._to_ready("conflict", f"#{one.pull.number} conflicts with `{one.pull.base}` in these files: merge "
                      f"`{one.pull.base}` into the branch, resolve them, and `pr open` again.\n\n{shown}", "other_way")
    one._write(None, None, [("pr.conflicted", {"pr": one.pull.number, "files": files, "other_pr": other_pr})])
    one._attempt("conflict", False, 0)
    one._end_pr("refused", "conflict")
