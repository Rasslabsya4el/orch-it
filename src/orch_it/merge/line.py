"""The merge line (#400 decision 2): `run_line` lands a repo's green pull requests on its base one by
one, the oldest ready first, under the repo's line lock, run on the machine under the owner's `gh`
(so every update and merge starts the required checks).

A member of the line is an open, non-draft pull request into the base, its head in this repo (no
fork), with a ready mark that no left mark followed; its ready time is its ready mark's. The line is the
only one that merges in the line's order (#414); the follow's mark-only path (#424) merges a pull request
that has no line. One run, until the line is empty or `line_minutes` pass:

1. every conflicting member leaves the line at once and its ticket goes back to ready with the
   files (the follow's conflict rule), so a conflict never holds the members behind it;
2. the head is the oldest ready member;
3. the head is served: behind -> brought up to date (`update_branch`, at most `max_updates` times a
   run, else it waits for the next run: `starved`); a conflict on the update -> the conflict rule;
   then its required checks of the head sha are waited for, polling every `poll_seconds`, at most
   `check_minutes` (`bound`: it stays in the line for the next run, the next member goes), with the
   follow run at each poll (a person's comment, an edit of its ticket, a red, no result: the follow
   moves the ticket and the head `left`); green and up to date -> its title and body are read fresh,
   and a closing keyword in either refuses it (`refused`: a left mark with the reason, nothing merged,
   the line goes on); else the line merges it itself with its head sha and the setting `merge_method`,
   and the follow closes its ticket at once (GitHub refusing the merge more than `max_updates` times:
   `refused`, left for the next run). A head that moved since the checks were read is refused by GitHub
   (the merge is pinned to the sha), so a pull request never lands on a tree whose checks were not read.

Events: a `line` step, run `line:<repo>@<start>` (started `base`, `prs`; ended `merged`, `left`,
reason `empty`, `next_run` or `bound`; `failed` with `error` when the run breaks; `refused` with
`lock_busy`/`lock_stale` when the lock is not had) and one `line.served` per turn under that run
(`pr`, `result`, `followed`, `waited_minutes`, `updates`), plus `pr.updated` per update.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orch_it.events import github_budget
from orch_it.events.store import Store
from orch_it.merge import lock, marks, record
from orch_it.merge.follow import FollowReport, follow, follow_conflict
from orch_it.merge.hub import Pull, PullHub
from orch_it.merge.pr_open import refers_to
from orch_it.merge.settings import MergeSettings, required_checks_now
from orch_it.tickets.park import ROLE

# what the follow says of a member that is still in the line: the line goes on serving it (a
# `raced` follow wrote nothing: the member waits a poll and is followed again)
_STAYS = ("waiting", "green", "behind", "rerun", "stuck", "raced")
LINE_ERROR_CODES = ("closing_keyword",)  # a merge the line will not make


def poll_wait(hub: PullHub, settings: MergeSettings) -> float:
    """The wait before the line's next read of its head (#414 decision 9): `poll_seconds`, longer while the account's
    request budget, as GitHub reports it now, is under the threshold. No reading keeps the interval."""

    left = hub.rate_limit()
    if left is None:
        return float(settings.poll_seconds)
    remaining, limit = left
    return github_budget.poll_interval(settings.poll_seconds, remaining, limit, github_budget.threshold())


class LineError(ValueError):
    """A merge the line will not make; `code` is one of `LINE_ERROR_CODES`, `detail` says why. The line
    takes the error in and turns it into that pull request's `refused` turn; it does not stop the run."""

    def __init__(self, code: str, detail: str = ""):
        if code not in LINE_ERROR_CODES:
            raise ValueError(f"not a merge line error code: {code}")
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class Served:
    pr: int
    ticket: int | None
    # a `line_result`: merged, left, starved (main moved too often), bound, refused (GitHub would not merge,
    # or a closing keyword in its text)
    result: str
    waited_minutes: float  # from its ready time to the end of its turn
    updates: int
    detail: str = ""  # the follow's outcome when it left


@dataclass(frozen=True)
class LineReport:
    repo: str
    base: str
    served: tuple[Served, ...]
    reason: str  # `empty`: no member left; `next_run`: the ones left wait for the next run; `bound`: the run's bound

    @property
    def merged(self) -> int:
        return sum(s.result == "merged" for s in self.served)

    @property
    def left(self) -> int:
        return sum(s.result == "left" for s in self.served)


@dataclass
class _Member:
    pull: Pull
    ready_at: datetime
    ticket: int | None


@dataclass
class _Line:
    hub: PullHub
    store: Store
    repo: str
    base: str
    settings: MergeSettings
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]
    role: str
    required: tuple[str, ...]
    owner: str
    deadline: datetime
    run_id: str
    for_next_run: set[int] = field(default_factory=set)  # members this run leaves for the next one
    turns: list[Served] = field(default_factory=list)
    ready_at: dict[int, datetime] = field(default_factory=dict)  # pr -> its ready time, as members() read it

    def run(self) -> tuple[list[Served], str]:
        turns = self.turns
        while self.clock() < self.deadline:
            members = self.members()
            for member in [m for m in members if m.pull.mergeable == "conflicting"]:
                self.end_turn(turns, self.conflict(member, self.hub.conflict_files(self.repo, self.base,
                                                                                    member.pull.number)))
            members = [m for m in self.members() if m.pull.number not in self.for_next_run]
            if not members:
                return turns, "next_run" if self.for_next_run else "empty"
            self.end_turn(turns, self.serve(members[0]))
        return turns, "bound"

    def end_turn(self, turns: list[Served], turn: Served) -> None:
        turns.append(turn)
        record.write(self.store, self.repo, turn.ticket, self.role, self.run_id, None, [("line.served", {
            "pr": turn.pr, "result": turn.result, "followed": turn.detail or None,
            "waited_minutes": turn.waited_minutes, "updates": turn.updates})])

    # members and holds

    def members(self) -> list[_Member]:
        found = []
        for pull in self.hub.open_pulls(self.repo, self.base):
            if pull.draft or (pull.head_repo or "").lower() != self.repo.lower():
                continue
            ready = marks.ready_since(marks.of_layer(self.hub.pull_comments(self.repo, pull.number), self.owner))
            if ready is None:
                continue
            found.append(_Member(pull, ready.at if ready else pull.created_at, refers_to(pull.body)))
            self.ready_at[pull.number] = found[-1].ready_at
        return sorted(found, key=lambda m: (m.ready_at, m.pull.number))

    # the head

    def serve(self, member: _Member) -> Served:
        number, updates, refused = member.pull.number, 0, 0
        turn_ends = min(self.deadline, self.clock() + timedelta(minutes=self.settings.check_minutes))
        while True:
            pull = self.hub.pull(self.repo, number)
            if pull is None or pull.state != "open":  # merged or closed by a person
                if pull is not None:
                    self.follow(number)
                result = "merged" if pull is not None and pull.state == "merged" else "left"
                return self.turn(member, result, updates, "" if result == "merged" else "closed")
            done = self.follow(number)
            if done.outcome not in _STAYS:
                return self.turn(member, "left", updates, done.outcome)
            if pull.behind:  # a run on a head behind the base would be thrown away: update now
                if updates >= self.settings.max_updates:
                    self.for_next_run.add(number)
                    return self.turn(member, "starved", updates)
                if not self.hub.update_branch(self.repo, number, pull.head_sha):
                    return self.conflict(member, self.hub.conflict_files(self.repo, self.base, number), updates)
                updates += 1
                fresh = self.hub.pull(self.repo, number)
                record.write(self.store, self.repo, member.ticket, self.role, self.run_id, None,
                             [record.pr_updated(number, pull.head_sha, fresh.head_sha if fresh else None)])
                turn_ends = min(self.deadline, self.clock() + timedelta(minutes=self.settings.check_minutes))
                continue
            if done.outcome == "green" and pull.behind == 0:
                try:
                    self.closing_check(number)
                except LineError as refusal:
                    return self.refuse(member, refusal, updates)
                sha = self.hub.merge(self.repo, number, pull.head_sha, self.settings.merge_method)
                if sha:
                    self.follow(number)  # its ticket closes now, whether the workflow runs or not
                    return self.turn(member, "merged", updates)
                refused += 1  # the base moved or a check changed between the read and the merge: read again
                if refused > self.settings.max_updates:
                    self.for_next_run.add(number)
                    return self.turn(member, "refused", updates)
            if self.clock() >= turn_ends:
                self.for_next_run.add(number)
                return self.turn(member, "bound", updates, done.outcome)
            self.sleep(poll_wait(self.hub, self.settings))

    def follow(self, number: int) -> FollowReport:
        return follow(self.hub, self.store, self.repo, number, base=self.base, settings=self.settings,
                      now=self.clock(), role=self.role, required=self.required, owner=self.owner, merge=False)

    def closing_check(self, number: int) -> None:
        """Before the merge: the title and body as they are now (a fresh read, so an edit after `pr open`
        counts). A closing keyword in either raises `LineError`, naming each keyword, its reference, the text
        and the line it starts on."""

        now = self.hub.pull(self.repo, number)
        if now is None:
            return
        why = marks.closing_refusal(number, now.title, now.body)
        if why:
            raise LineError("closing_keyword", why)

    def refuse(self, member: _Member, refusal: LineError, updates: int) -> Served:
        """A pull request whose text closes a ticket by a word leaves the line the way `follow._leave` takes
        one out: a left mark carrying the reason, so no later run serves it until a new ready mark. The line
        goes on with the pull requests behind it; the ticket stays open."""

        number = member.pull.number
        self.hub.comment(self.repo, number, marks.left_comment(refusal.code, refusal.detail))
        return self.turn(member, "refused", updates, refusal.detail)

    def conflict(self, member: _Member, files: list[str], updates: int = 0) -> Served:
        done = follow_conflict(self.hub, self.store, self.repo, member.pull.number, files, settings=self.settings,
                               now=self.clock(), role=self.role, owner=self.owner)
        if done.outcome == "raced":
            self.for_next_run.add(member.pull.number)
        return self.turn(member, "left", updates, done.outcome)

    def turn(self, member: _Member, result: str, updates: int, detail: str = "") -> Served:
        waited = (self.clock() - member.ready_at).total_seconds() / 60
        return Served(member.pull.number, member.ticket, result, round(waited, 1), updates, detail)


def run_line(hub: PullHub, store: Store, repo: str, base: str, *, settings: MergeSettings,
             clock: Callable[[], datetime] = lambda: datetime.now(UTC), sleep: Callable[[float], None] = time.sleep,
             root: Path | None = None, holder: str | None = None, on_wait: Callable[[lock.Holder], None] | None = None,
             role: str = ROLE, required: tuple[str, ...] | None = None,
             owner: str | None = None) -> LineReport:
    """Run the repo's merge line once; see the module text. Another line holding the lock past
    `lock_wait_minutes`, or a stale lock, is `LockError` (and a `line` step ended `refused`); a run
    that breaks ends its step `failed` (`error`)."""

    if required is None:
        required = required_checks_now(repo)
    run = f"line:{repo}@{clock().isoformat()}"
    try:
        with lock.hold(repo, holder or holder_of(repo, base), settings=settings, root=root, clock=clock, sleep=sleep,
                       on_wait=on_wait):
            line = _Line(hub, store, repo, base, settings, clock, sleep, role, required,
                         (owner or repo.split("/")[0]).lower(), clock() + timedelta(minutes=settings.line_minutes), run)
            record.write(store, repo, None, role, run, "line",
                         [("step.started", {"base": base, "prs": len(line.members())})])
            try:
                turns, reason = line.run()
            except Exception:
                record.write(store, repo, None, role, run, "line",
                             [("step.ended", {"outcome": "failed", "reason": "error"})])
                raise
    except lock.LockError as e:
        if e.code in ("lock_busy", "lock_stale"):
            record.write(store, repo, None, role, run, "line",
                         [("step.started", {"base": base, "prs": None}),
                          ("step.ended", {"outcome": "refused", "reason": e.code})])
        raise
    report = LineReport(repo, base, tuple(turns), reason)
    record.write(store, repo, None, role, run, "line",
                 [("step.ended", {"outcome": "passed", "reason": reason, "merged": report.merged,
                                  "left": report.left})])
    return report


def holder_of(repo: str, base: str) -> str:
    """How a line names itself in its lock."""

    return f"merge line of {repo} into {base}"
