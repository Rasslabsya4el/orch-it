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
its own after), the required suite runs once on that tree through the place rule. The pass replaces no required check:
the combined tree is a branch, and the line waits (every `poll_seconds`, at most `check_minutes`, by the clock) until
every name of `required_checks` is green on that exact sha, whichever place posted it; only then does it move the base
forward to the tested commit by one non-forced ref update (the base's sha is the tested tree's, each pull request ends
merged), which the base's own protection accepts or refuses and the line never pushes around. Where GitHub's runs would not start (Actions off, the account's minutes spent, a queue past its bound) and the suite
passed at another place, the line posts PASSED for each required name that run covered (the test jobs on its OS) and
nobody posted on that sha; a name it did not cover (a scan, a test job of another OS) stays unposted and holds the
batch, as does any name nobody posted with GitHub on. A repo whose base
protects nothing is held by this same wait and refusal: the line alone holds the required checks. A name still not green when the wait ends (note `required
checks not green on <sha>: <names>`) lands nothing and stops the run: every member not landed stays for the next run.
A red test or a red required name splits the batch in halves, each half is combined again from the base as it then is
and checked, down to the one red pull request, which goes back to its ticket by the follow's red rule while the others
land. A red whose every
failing test also fails on main (an open red-on-main ticket covers them, and a run of those ids alone on the sha main had
when the batch was made fails them all) is not a member's fault: the line stops there (note `main is red on <ids>`), the
members stay for the next run, none is sent back. A base that moved under a green pass rebuilds the batch on the new
base (without the members that left it), at most `max_updates` times. A landing counts as merged only when GitHub says
the pull request is merged (polled up to a minute; else `bound`, detail `landed_pending`, for the next run). A base whose
protection refuses the landing (note `batch landing refused: <why>`) sends the rest of the run one by one. A line that
batches first deletes the `orch-batch/` branches older than `lock_stale_minutes`. `batch_size` 1, or no suite, is the
one-by-one line above.

Before each ask of the place rule for a run (the batch's pass, the run of a failing test's ids on main) the line asks its
`priority_hold` (#414 item 4, `testruns/priority.py`): while it names a repo of the account with a higher
`github_priority` that has a ready member and the account's jobs are at their concurrency, the line waits (every
`poll_seconds`) and asks again; held to the run's bound, nothing starts, every member stays for the next run (note
`start held for <repo>`, detail `priority_hold`).

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

from orch_it.events.store import Store
from orch_it.merge import batch, lock, marks, record, red_main
from orch_it.merge.follow import SHORT_SHA, FollowReport, follow, follow_conflict, follow_red
from orch_it.merge.hub import FAILED as CHECK_FAILED
from orch_it.merge.hub import PASSED as CHECK_PASSED
from orch_it.merge.hub import LandingRefused, Pull, PullHub
from orch_it.merge.pr_open import refers_to
from orch_it.merge.settings import MergeSettings, required_checks_now
from orch_it.testruns.github_place import budget_poll
from orch_it.testruns.places import RUNS_NOT_STARTING
from orch_it.tickets.park import ROLE

# the description of a required name the line posts after a suite that passed at a place other than GitHub's
POSTED_NOTE = "the test suite passed on this commit at a place other than GitHub; posted by the merge line"

# what the follow says of a member that is still in the line: the line goes on serving it (a
# `raced` follow wrote nothing: the member waits a poll and is followed again)
_STAYS = ("waiting", "green", "behind", "rerun", "stuck", "raced")
LINE_ERROR_CODES = ("closing_keyword",)  # a merge the line will not make
LAND_POLL_SECONDS, LAND_WAIT_SECONDS = 5, 60  # after a batch moved the base: GitHub marks the pull requests merged


def poll_wait(hub: PullHub, settings: MergeSettings) -> float:
    """The wait before the line's next read of its head (#414 decision 9): `poll_seconds`, longer while the account's
    request budget, as GitHub reports it now, is under the threshold (`budget_poll`). No reading keeps the interval."""

    return budget_poll(settings.poll_seconds, hub.rate_limit())


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
    notes: tuple[str, ...] = ()  # what the run noticed beside its turns: a landing the base refused, a main red

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
    base_sha: str  # the sha the base had when the batch was made
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
    priority_hold: Callable[[], str | None] | None = None  # the repo whose ready member holds the start; None: none does
    held: str | None = None  # the run stops, nothing is sent back; why (a turn's detail): main is red on every test a
    # batch failed (`main_red`), a required name stays not green (`checks_missing`), or the start is held for a repo
    # of higher priority to the run's bound (`priority_hold`)
    refused: str | None = None  # the base's own reason for refusing a batch landing: the rest goes one by one
    notes: list[str] = field(default_factory=list)

    def run(self) -> tuple[list[Served], str]:
        turns = self.turns
        if self.suite is not None and self.settings.batch_size > 1:
            self.clean_batches()
        while self.clock() < self.deadline:
            members = self.members()
            for member in [m for m in members if m.pull.mergeable == "conflicting"]:
                self.end_turn(turns, self.conflict(member, self.hub.conflict_files(self.repo, self.base,
                                                                                    member.pull.number)))
            members = [m for m in self.members() if m.pull.number not in self.for_next_run]
            if not members:
                return turns, "next_run" if self.for_next_run else "empty"
            if self.suite is not None and self.settings.batch_size > 1 and not self.refused:
                self.serve_batch(members[:self.settings.batch_size])
                if self.held:
                    return turns, "next_run"
            else:
                self.end_turn(turns, self.serve(members[0]))
        return turns, "bound"

    def clean_batches(self) -> None:
        """A line that died leaves its batch branch: each one older than `lock_stale_minutes` (or with no time in its
        name) goes; a young one may belong to a line on another machine."""

        now = self.clock()
        for name in self.hub.batch_branches(self.repo):
            if batch.stale(name, now, self.settings.lock_stale_minutes):
                self.hub.end_batch(self.repo, name)

    def end_turn(self, turns: list[Served], turn: Served) -> None:
        turns.append(turn)
        record.write(self.store, self.repo, turn.ticket, self.role, self.run_id, None, [("line.served", {
            "pr": turn.pr, "result": turn.result, "followed": turn.detail or None,
            "waited_minutes": turn.waited_minutes, "updates": turn.updates})])

    # members and holds

    def members(self) -> list[_Member]:
        found = []
        for pull, ready in marks.ready_pulls(self.hub, self.repo, self.base, self.owner):
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
        while group and not self.refused and not self.held:
            if self.clock() >= self.deadline:
                return self.postpone(group, "bound")
            made = self.combine(group)
            group = [m for m in group if m.pull.number not in made.gone]  # a rebuild serves only the stayers
            try:
                if made.included and (rival := self.wait_for_turn()):
                    self.yield_to(made, rival)
                elif made.included:
                    result, by_check, unmet = self.tested(made)
                    if unmet:
                        self.hold(made, unmet)
                    elif result.outcome == batch.PASSED:
                        if not self.land(made):
                            if self.refused:
                                return  # the members stay in the line and go one by one
                            rebuilt += 1  # the base or a head moved under the pass: the batch is made again
                            if rebuilt > self.settings.max_updates:
                                return self.postpone(group, "starved")
                            continue
                    elif (result.outcome == batch.FAILED and result.failing and by_check
                          and self.name_red_on_main(made, result.failing)):
                        pass  # main's head is red on every one of these names: nothing to split, nothing to send back
                    elif result.outcome == batch.FAILED and result.failing and len(made.included) == 1:
                        self.red(made.included[0], list(result.failing), by_check)
                    elif (result.outcome == batch.FAILED and result.failing and not by_check
                          and self.red_on_main(made, result.failing)):
                        pass  # main itself is red on all of it: nothing to split, nothing to send back
                    elif result.outcome == batch.FAILED and result.failing:
                        half = len(made.included) // 2
                        self.settle(made.included[:half])
                        self.settle(made.included[half:])
                    else:  # no result, or a red that names no test: no member is blamed
                        self.postpone(made.included, "bound", "no_result")
            finally:
                self.hub.end_batch(self.repo, made.branch)
            group = made.apart
        if group and self.held:  # a hold ends the run: the members it did not reach wait for the next one
            self.postpone(group, "bound", self.held)

    def tested(self, made: _Batch) -> tuple[batch.SuiteResult, bool, tuple[str, ...]]:
        """What the combined tree earned: the suite's result, and when the suite passed the required names on its sha
        too. `(result, by_check, unmet)`: a name red turns a pass into a failure naming the names (`by_check`); names
        still not green when the wait ends are `unmet` (the batch lands nothing)."""

        result = self.suite(made.tip)
        if result.outcome != batch.PASSED:
            return result, False, ()
        if result.github_out in RUNS_NOT_STARTING:
            self.post_missing(made.tip, result.covered)
        red, unmet = self.wait_for_names(made.tip)
        if red:
            return batch.SuiteResult(batch.FAILED, red), True, ()
        return result, False, unmet

    def post_missing(self, sha: str, covered: tuple[str, ...]) -> None:
        """GitHub's runs would not start (Actions off, minutes spent, a queue past its bound), so the suite that just
        passed ran at another place and no workflow will post the required names. The pass speaks for the names its run
        covered (`covered`: the jobs on the OS it ran on, and the OS-free names, tied to no OS, which run where the suite
        runs): the line posts PASSED for each of those nobody posted on `sha` (a name some place posted, red or pending,
        is left as it is). A required name of another OS never gets a post no run covered: it stays unposted and holds
        the batch (`checks_missing`). Only after a passed suite."""

        posted = {check.name for check in self.hub.checks(self.repo, sha, statuses=True)}
        for name in self.required:
            if name in covered and name not in posted:
                self.hub.post_status(self.repo, sha, name, CHECK_PASSED, POSTED_NOTE)

    def wait_for_names(self, sha: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Poll the checks of `sha` (`budget_poll` through `poll_wait`, never a bare sleep) until every name of
        `required` is green: `((), ())`. A name red ends the wait at once: `(red names, ())`. At `check_minutes` (or
        the run's bound) the names not green: `((), those)`."""

        ends = self.check_ends()
        while True:
            states = {check.name: check.state for check in self.hub.checks(self.repo, sha, statuses=True)}
            red = tuple(name for name in self.required if states.get(name) == CHECK_FAILED)
            unmet = tuple(name for name in self.required if states.get(name) != CHECK_PASSED)
            if red:
                return red, ()
            if not unmet or self.clock() >= ends:
                return (), unmet
            self.sleep(poll_wait(self.hub, self.settings))

    def check_ends(self) -> datetime:
        """When a wait for checks that starts now ends: `check_minutes` from now, or the run's bound if sooner."""

        return min(self.deadline, self.clock() + timedelta(minutes=self.settings.check_minutes))

    def wait_for_turn(self) -> str | None:
        """Before a run is asked for: None when no repo of higher priority holds the start; else wait (`poll_wait`) and
        ask again, and the repo still holding it at the run's bound."""

        if self.priority_hold is None:
            return None
        while (rival := self.priority_hold()) is not None:
            if self.clock() >= self.deadline:
                return rival
            self.sleep(poll_wait(self.hub, self.settings))
        return None

    def yield_to(self, made: _Batch, rival: str) -> None:
        """The start held for `rival` to the run's bound: nothing runs, the run stops, no member is sent back."""

        self.held = "priority_hold"
        self.notes.append(f"start held for {rival}: a ready member of higher priority, the account at its concurrency")
        self.postpone(made.included, "bound", "priority_hold")

    def hold(self, made: _Batch, unmet: tuple[str, ...]) -> None:
        """A required name not green when the wait ended: nothing lands, the run stops, no member is sent back."""

        self.held = "checks_missing"
        self.notes.append(f"required checks not green on {made.tip[:SHORT_SHA]}: {', '.join(unmet)}")
        self.postpone(made.included, "bound", "checks_missing")

    def red_on_main(self, made: _Batch, failing: tuple[str, ...]) -> bool:
        """Whether every failing id of `made` is red on main too: an open red-on-main ticket covers them all, and a
        run of just those ids on the sha main had when the batch was made fails them all. Then the line holds: the
        members wait for main's next sha, none is sent back."""

        ids = {red_main.normal(test_id) for test_id in failing}
        reds = red_main.open_reds(self.hub, self.repo)
        known = set().union(*(red.ids for red in reds if red.ids is not None))
        if not (any(red.covers(ids) for red in reds) or ids <= known):
            return False
        if rival := self.wait_for_turn():
            self.yield_to(made, rival)
            return True
        on_main = self.suite(made.base_sha, only=failing)
        if on_main.outcome != batch.FAILED or not ids <= {red_main.normal(test_id) for test_id in on_main.failing}:
            return False
        self.held = "main_red"
        self.notes.append(f"main is red on {', '.join(sorted(ids))}")
        self.postpone(made.included, "bound", "main_red")
        return True

    def name_red_on_main(self, made: _Batch, red: tuple[str, ...]) -> bool:
        """Whether every red required name of `made` is red on the head main had when the batch was made (a name main
        has no result for, or green or pending there, is not red on main). Then the line holds as `red_on_main` does:
        the members wait for main's next sha, none is sent back."""

        on_main = {check.name: check.state for check in self.hub.checks(self.repo, made.base_sha, statuses=True)}
        if not all(on_main.get(name) == CHECK_FAILED for name in red):
            return False
        if rival := self.wait_for_turn():
            self.yield_to(made, rival)
            return True
        self.held = "main_red"
        self.notes.append(f"main is red on {', '.join(sorted(red))}")
        self.postpone(made.included, "bound", "main_red")
        return True

    def combine(self, group: list[_Member]) -> _Batch:
        """A branch from the base holding the head of each member of `group` that combines, in order. A pull request
        gone or closed since it was read leaves the line; the first one that conflicts with the base itself takes the
        conflict rule; one that conflicts with the ones before it is `apart`."""

        self.batches_made += 1
        branch = batch.branch_name(self.clock(), self.batches_made)
        base_sha = self.hub.start_batch(self.repo, self.base, branch)
        made = _Batch(branch, base_sha, [], {}, [], base_sha)
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
        try:
            if not self.hub.advance_base(self.repo, self.base, made.tip):
                return False
        except LandingRefused as refusal:  # the base's protection: no batch lands here, the members go one by one
            self.refused = refusal.reason
            self.notes.append(f"batch landing refused: {refusal.reason}")
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

    def red(self, member: _Member, failing: list[str], by_check: bool = False) -> None:
        """The member found red alone goes back by the follow's red rule; `failing` are test ids, or (`by_check`) the
        required check names that were red on its tree."""

        names = ", ".join(failing if by_check else self.required)
        done = follow_red(self.hub, self.store, self.repo, member.pull.number, names, failing,
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
        turn_ends = self.check_ends()
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
                turn_ends = self.check_ends()
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
             owner: str | None = None, suite: batch.Suite | None = None,
             priority_hold: Callable[[], str | None] | None = None) -> LineReport:
    """Run the repo's merge line once; see the module text. Another line holding the lock past
    `lock_wait_minutes`, or a stale lock, is `LockError` (and a `line` step ended `refused`); a run
    that breaks ends its step `failed` (`error`). `suite`: the required suite on a commit (`batch.place_rule_suite`),
    which the batches need; none, or `batch_size` 1, serves one pull request at a time. `priority_hold`: what the line
    asks before it asks for a run (`testruns/priority.hold_of`); None: nothing holds the start."""

    if required is None:
        required = required_checks_now(repo)
    run = f"line:{repo}@{clock().isoformat()}"
    try:
        with lock.hold(repo, holder or holder_of(repo, base), settings=settings, root=root, clock=clock, sleep=sleep,
                       on_wait=on_wait):
            line = _Line(hub, store, repo, base, settings, clock, sleep, role, required,
                         (owner or repo.split("/")[0]).lower(), clock() + timedelta(minutes=settings.line_minutes), run,
                         suite=suite, priority_hold=priority_hold)
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
    report = LineReport(repo, base, tuple(turns), reason, tuple(line.notes))
    record.write(store, repo, None, role, run, "line",
                 [("step.ended", {"outcome": "passed", "reason": reason, "merged": report.merged,
                                  "left": report.left})])
    return report


def holder_of(repo: str, base: str) -> str:
    """How a line names itself in its lock."""

    return f"merge line of {repo} into {base}"
