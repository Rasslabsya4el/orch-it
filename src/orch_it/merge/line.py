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

With the setting `batch_size` over 1 and a `suite` given (#414 item 5), steps 2 and 3 are one pass for up to
`batch_size` members instead of one pass each: the oldest ready members are combined on one branch from the base (each
head merged in line order; a member that does not combine with the ones before it is left apart and goes in a batch of
its own after), the required suite runs once on that tree through the place rule, and green lands them all by moving
the base forward to the tested commit (the base's sha is the tested tree's, each pull request ends merged); red
splits the batch in halves, each half is combined again from the base as it then is and checked, down to the one
red pull request, which goes back to its ticket by the follow's red rule while the others land. A base that moved
under a green pass rebuilds the batch on the new base, at most `max_updates` times. `batch_size` 1, or no suite, is
the one-by-one line above.

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
from orch_it.merge import batch, lock, marks, record
from orch_it.merge.follow import FollowReport, follow, follow_conflict, follow_red
from orch_it.merge.hub import Pull, PullHub
from orch_it.merge.pr_open import refers_to
from orch_it.merge.settings import MergeSettings, required_checks_now
from orch_it.tickets.park import ROLE

# what the follow says of a member that is still in the line: the line goes on serving it (a
# `raced` follow wrote nothing: the member waits a poll and is followed again)
_STAYS = ("waiting", "green", "behind", "rerun", "stuck", "raced")
LINE_ERROR_CODES = ("closing_keyword",)  # a merge the line will not make
LAND_POLL_SECONDS, LAND_WAIT_SECONDS = 5, 60  # after a batch moved the base: GitHub marks the pull requests merged


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
class _Batch:
    """One combined branch: `included` members in line order with the head each was merged at, those `apart` that did
    not combine with the ones before it."""

    branch: str
    tip: str
    included: list[_Member]
    heads: dict[int, str]
    apart: list[_Member]
    gone: set[int] = field(default_factory=set)  # members combine ended (closed, or in conflict with the base)


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
    suite: batch.Suite | None = None  # the required suite on a commit; None: no batches
    batches_made: int = 0

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
            if self.suite is not None and self.settings.batch_size > 1:
                self.serve_batch(members[:self.settings.batch_size])
            else:
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

    # a batch

    def serve_batch(self, candidates: list[_Member]) -> None:
        """The oldest ready members as one batch: each is followed and its text checked as `serve` does, then the
        ones that stay are settled together."""

        group = []
        for member in candidates:
            done = self.follow(member.pull.number)
            if done.outcome not in _STAYS:
                self.end_turn(self.turns, self.turn(member, "left", 0, done.outcome))
                continue
            try:
                self.closing_check(member.pull.number)
            except LineError as refusal:
                self.end_turn(self.turns, self.refuse(member, refusal, 0))
                continue
            group.append(member)
        self.settle(group)

    def settle(self, group: list[_Member]) -> None:
        """Check `group` as one batch and land it; on red split the batch and settle each half; then the members that
        did not combine, as a group of their own."""

        rebuilt = 0
        while group:
            if self.clock() >= self.deadline:
                return self.postpone(group, "bound")
            made = self.combine(group)
            group = [m for m in group if m.pull.number not in made.gone]  # a rebuild serves only the stayers
            try:
                if made.included:
                    result = self.suite(made.tip)
                    if result.outcome == batch.PASSED:
                        if not self.land(made):
                            rebuilt += 1  # the base or a head moved under the pass: the batch is made again
                            if rebuilt > self.settings.max_updates:
                                return self.postpone(group, "starved")
                            continue
                    elif result.outcome == batch.FAILED and result.failing and len(made.included) == 1:
                        self.red(made.included[0], list(result.failing))
                    elif result.outcome == batch.FAILED and result.failing:
                        half = len(made.included) // 2
                        self.settle(made.included[:half])
                        self.settle(made.included[half:])
                    else:  # no result, or a red that names no test: no member is blamed
                        self.postpone(made.included, "bound", "no_result")
            finally:
                self.hub.end_batch(self.repo, made.branch)
            group = made.apart

    def combine(self, group: list[_Member]) -> _Batch:
        """A branch from the base holding the head of each member of `group` that combines, in order. A pull request
        gone or closed since it was read leaves the line; the first one that conflicts with the base itself takes the
        conflict rule; one that conflicts with the ones before it is `apart`."""

        self.batches_made += 1
        branch = f"orch-batch/{int(self.clock().timestamp())}-{self.batches_made}"
        made = _Batch(branch, self.hub.start_batch(self.repo, self.base, branch), [], {}, [])
        for member in group:
            number = member.pull.number
            pull = self.hub.pull(self.repo, number)
            if pull is None or pull.state != "open":
                self.follow(number)
                self.end_turn(self.turns, self.turn(member, "left", 0, "closed"))
                made.gone.add(number)
                continue
            tip = self.hub.add_to_batch(self.repo, branch, pull.head_sha)
            if tip is not None:
                made.tip = tip
                made.included.append(member)
                made.heads[number] = pull.head_sha
            elif made.included:
                made.apart.append(member)
            else:
                self.end_turn(self.turns, self.conflict(member, self.hub.conflict_files(self.repo, self.base, number)))
                made.gone.add(number)
        return made

    def land(self, made: _Batch) -> bool:
        """Move the base to the tested commit and settle every member; False, nothing landed, when a head is not the
        one tested or the base is no longer where the batch started."""

        for member in made.included:
            pull = self.hub.pull(self.repo, member.pull.number)
            if pull is None or pull.state != "open" or pull.head_sha != made.heads[member.pull.number]:
                return False
        if not self.hub.advance_base(self.repo, self.base, made.tip):
            return False
        merged, pending = self.confirmed(made.included)
        for member in merged:
            self.follow(member.pull.number)  # its ticket closes now, whether the workflow runs or not
            self.end_turn(self.turns, self.turn(member, "merged", 0))
        self.postpone(pending, "bound", "landed_pending")
        return True

    def confirmed(self, members: list[_Member]) -> tuple[list[_Member], list[_Member]]:
        """The members GitHub says are merged, polled every `LAND_POLL_SECONDS` up to `LAND_WAIT_SECONDS` after the base
        moved, and those still open after it (not followed: they wait for the next run); one closed unmerged ends
        `left`."""

        waiting, merged = list(members), []
        until = self.clock() + timedelta(seconds=LAND_WAIT_SECONDS)
        while waiting:
            still = []
            for member in waiting:
                pull = self.hub.pull(self.repo, member.pull.number)
                if pull is not None and pull.state == "open":
                    still.append(member)
                elif pull is not None and pull.state == "merged":
                    merged.append(member)
                else:
                    self.follow(member.pull.number)
                    self.end_turn(self.turns, self.turn(member, "left", 0, "closed"))
            waiting = still
            if waiting and self.clock() < until:
                self.sleep(LAND_POLL_SECONDS)
            elif waiting:
                break
        return merged, waiting

    def red(self, member: _Member, failing: list[str]) -> None:
        done = follow_red(self.hub, self.store, self.repo, member.pull.number, ", ".join(self.required), failing,
                          settings=self.settings, now=self.clock(), role=self.role, owner=self.owner)
        if done.outcome == "raced":
            self.for_next_run.add(member.pull.number)
        self.end_turn(self.turns, self.turn(member, "left", 0, done.outcome))

    def postpone(self, group: list[_Member], result: str, detail: str = "") -> None:
        for member in group:
            self.for_next_run.add(member.pull.number)
            self.end_turn(self.turns, self.turn(member, result, 0, detail))

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
             owner: str | None = None, suite: batch.Suite | None = None) -> LineReport:
    """Run the repo's merge line once; see the module text. Another line holding the lock past
    `lock_wait_minutes`, or a stale lock, is `LockError` (and a `line` step ended `refused`); a run
    that breaks ends its step `failed` (`error`). `suite`: the required suite on a commit (`batch.place_rule_suite`),
    which the batches need; none, or `batch_size` 1, serves one pull request at a time."""

    if required is None:
        required = required_checks_now(repo)
    run = f"line:{repo}@{clock().isoformat()}"
    try:
        with lock.hold(repo, holder or holder_of(repo, base), settings=settings, root=root, clock=clock, sleep=sleep,
                       on_wait=on_wait):
            line = _Line(hub, store, repo, base, settings, clock, sleep, role, required,
                         (owner or repo.split("/")[0]).lower(), clock() + timedelta(minutes=settings.line_minutes), run,
                         suite=suite)
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
