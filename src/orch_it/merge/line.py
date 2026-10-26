"""The merge line (#400 decision 2): `run_line` lands a repo's green pull requests on its base one by
one, the oldest ready first, under the repo's line lock, run on the machine under the owner's `gh`
(so every update and merge starts the required checks).

A member of the line is an open, non-draft pull request into the base, its head in this repo (no
fork), with a ready mark that no left mark followed, or with auto-merge on; its ready time is its
ready mark's (else its creation). One run, until the line is empty or `line_minutes` pass:

1. every conflicting member leaves the line at once and its ticket goes back to ready with the
   files (the follow's conflict rule), so a conflict never holds the members behind it;
2. the head is the oldest ready member. Every other member that is up to date with the base has
   its auto-merge turned off for as long as it is (held): GitHub's own auto-merge would merge it
   as soon as it is green, out of the line's order, and make the head behind again. A held member
   gets it back once it is behind (the head merged) or is the head, and at the end of the run;
3. the head is served: behind → brought up to date (`update_branch`, at most `max_updates` times a
   run, else it waits for the next run: `starved`); a conflict on the update → the conflict rule;
   then its required checks of the head sha are waited for, polling every `poll_seconds`, at most
   `check_minutes` (`bound`: it stays in the line for the next run, the next member goes), with the
   follow run at each poll (a person's comment, an edit of its ticket, a red, no result: the follow
   moves the ticket and the head `left`); green and up to date → merged with its head sha (the
   follow closes its ticket from GitHub's own event).

Events: a `line` step (started `base`, `prs`; ended `merged`, `left`, reason `empty` or `bound`, or
`refused` with `lock_busy`/`lock_stale` when the lock is not had) and one `line.served` per head
(`pr`, `result`, `followed`, `waited_minutes`, `updates`), plus `pr.updated` per update.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orch_it.events.store import Store
from orch_it.merge import lock, marks, record
from orch_it.merge.follow import FollowReport, follow, follow_conflict
from orch_it.merge.hub import Pull, PullHub
from orch_it.merge.pr_open import refers_to
from orch_it.merge.settings import MergeSettings
from orch_it.testruns.github import DEFAULT_REQUIRED_JOBS

ROLE = "script"
# what the follow says of a head that is still in the line: the line goes on serving it
_STAYS = ("waiting", "green", "behind", "rerun", "stuck")


@dataclass(frozen=True)
class Served:
    pr: int
    ticket: int | None
    result: str  # merged, left, starved, bound
    waited_minutes: float  # from its ready time to the end of its turn
    updates: int
    detail: str = ""  # the follow's outcome when it left


@dataclass(frozen=True)
class LineReport:
    repo: str
    base: str
    served: tuple[Served, ...]
    reason: str  # `empty`: no member left; `bound`: the run's bound came first


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
    held: dict[int, str] = field(default_factory=dict)  # pr -> the auto-merge method it had
    passed_by: set[int] = field(default_factory=set)  # members this run leaves for the next one

    def run(self) -> tuple[list[Served], str]:
        served: list[Served] = []
        while self.clock() < self.deadline:
            members = self.members()
            for member in [m for m in members if m.pull.mergeable == "conflicting"]:
                served.append(self._conflict(member, self.hub.conflict_files(self.repo, self.base, member.pull.number)))
            members = [m for m in self.members() if m.pull.number not in self.passed_by]
            if not members:
                return served, "empty"
            self.hold_others(members[0].pull.number, members)
            served.append(self.serve(members[0]))
            self.record_served(served[-1])
        return served, "bound"

    # members and holds

    def members(self) -> list[_Member]:
        found = []
        for pull in self.hub.open_pulls(self.repo, self.base):
            if pull.draft or (pull.head_repo or "").lower() != self.repo.lower():
                continue
            ready = marks.ready_since(marks.of_layer(self.hub.pull_comments(self.repo, pull.number), self.owner))
            if ready is None and pull.auto_merge is None and pull.number not in self.held:
                continue
            found.append(_Member(pull, ready.at if ready else pull.created_at, refers_to(pull.body)))
        return sorted(found, key=lambda m: (m.ready_at, m.pull.number))

    def hold_others(self, head: int, members: list[_Member] | None = None) -> None:
        """Auto-merge off on every member but the head while it is up to date; back on once it is
        behind or is the head."""

        for member in members if members is not None else self.members():
            number, pull = member.pull.number, member.pull
            if number == head or pull.behind:
                if number in self.held:
                    self.hub.set_auto_merge(self.repo, number, self.held.pop(number))
            elif pull.auto_merge is not None and number not in self.held:
                self.hub.set_auto_merge(self.repo, number, None)
                self.held[number] = pull.auto_merge

    def release_all(self) -> None:
        for number, method in list(self.held.items()):
            pull = self.hub.pull(self.repo, number)
            if pull is not None and pull.state == "open":
                self.hub.set_auto_merge(self.repo, number, method)
            del self.held[number]

    # the head

    def serve(self, member: _Member) -> Served:
        number, updates, refused = member.pull.number, 0, 0
        turn_ends = min(self.deadline, self.clock() + timedelta(minutes=self.settings.check_minutes))
        while True:
            pull = self.hub.pull(self.repo, number)
            if pull is None or pull.state != "open":
                result = "merged" if pull is not None and pull.state == "merged" else "left"
                return self._served(member, result, updates, "" if result == "merged" else "closed")
            done = self._follow(number)
            if done.outcome not in _STAYS:
                return self._served(member, "left", updates, done.outcome)
            if pull.behind:  # a run on a head behind the base would be thrown away: update now
                if updates >= self.settings.max_updates:
                    self.passed_by.add(number)
                    return self._served(member, "starved", updates)
                if not self.hub.update_branch(self.repo, number, pull.head_sha):
                    files = self.hub.conflict_files(self.repo, self.base, number)
                    return self._conflict(member, files, updates)
                updates += 1
                fresh = self.hub.pull(self.repo, number)
                record.write(self.store, self.repo, member.ticket, self.role, None, None, [("pr.updated", {
                    "pr": number, "old_head": pull.head_sha, "new_head": fresh.head_sha if fresh else None,
                    "forced": False, "base_moved": True, "runs_cancelled": None})])
                turn_ends = min(self.deadline, self.clock() + timedelta(minutes=self.settings.check_minutes))
                continue
            if done.outcome == "green":
                sha = self.hub.merge(self.repo, number, pull.head_sha, pull.auto_merge or self.settings.merge_method)
                if sha:
                    return self._served(member, "merged", updates)
                refused += 1  # the base moved or a check changed between the read and the merge: read again
                if refused > self.settings.max_updates:
                    self.passed_by.add(number)
                    return self._served(member, "refused", updates)
                continue
            if self.clock() >= turn_ends:
                self.passed_by.add(number)
                return self._served(member, "bound", updates, done.outcome)
            self.sleep(self.settings.poll_seconds)
            self.hold_others(number)

    def _follow(self, number: int) -> FollowReport:
        return follow(self.hub, self.store, self.repo, number, base=self.base, settings=self.settings,
                      now=self.clock(), role=self.role, required=self.required, owner=self.owner)

    def _conflict(self, member: _Member, files: list[str], updates: int = 0) -> Served:
        done = follow_conflict(self.hub, self.store, self.repo, member.pull.number, files, settings=self.settings,
                               now=self.clock(), role=self.role, owner=self.owner)
        self.held.pop(member.pull.number, None)
        return self._served(member, "left", updates, done.outcome)

    def _served(self, member: _Member, result: str, updates: int, detail: str = "") -> Served:
        waited = (self.clock() - member.ready_at).total_seconds() / 60
        return Served(member.pull.number, member.ticket, result, round(waited, 1), updates, detail)

    def record_served(self, served: Served) -> None:
        record.write(self.store, self.repo, served.ticket, self.role, None, None, [("line.served", {
            "pr": served.pr, "result": served.result, "followed": served.detail or None,
            "waited_minutes": served.waited_minutes, "updates": served.updates})])


def run_line(hub: PullHub, store: Store, repo: str, base: str, *, settings: MergeSettings,
             clock: Callable[[], datetime] = lambda: datetime.now(UTC), sleep: Callable[[float], None] = time.sleep,
             root: Path | None = None, holder: str | None = None, on_wait: Callable[[lock.Holder], None] | None = None,
             role: str = ROLE, required: tuple[str, ...] = tuple(DEFAULT_REQUIRED_JOBS),
             owner: str | None = None) -> LineReport:
    """Run the repo's merge line once; see the module text. Another line holding the lock past
    `lock_wait_minutes`, or a stale lock, is `LockError` (and a `line` step ended `refused`)."""

    run = f"line:{repo}@{clock().isoformat()}"
    holder = holder or f"merge line of {repo} into {base}"
    try:
        with lock.hold(repo, holder, settings=settings, root=root, clock=clock, sleep=sleep, on_wait=on_wait):
            line = _Line(hub, store, repo, base, settings, clock, sleep, role, required,
                         (owner or repo.split("/")[0]).lower(), clock() + timedelta(minutes=settings.line_minutes))
            record.write(store, repo, None, role, run, "line",
                         [("step.started", {"base": base, "prs": len(line.members())})])
            try:
                served, reason = line.run()
            finally:
                line.release_all()
    except lock.LockError as e:
        if e.code in ("lock_busy", "lock_stale"):
            record.write(store, repo, None, role, run, "line",
                         [("step.started", {"base": base, "prs": None}),
                          ("step.ended", {"outcome": "refused", "reason": e.code})])
        raise
    merged = sum(s.result == "merged" for s in served)
    record.write(store, repo, None, role, run, "line",
                 [("step.ended", {"outcome": "passed", "reason": reason, "merged": merged,
                                  "left": sum(s.result == "left" for s in served)})])
    return LineReport(repo, base, tuple(served), reason)
