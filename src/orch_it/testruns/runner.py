"""One test run, start to end, written to the store (#394 decisions 1-5).

`run_tests` reads the ids file, writes the `test_run` `step.started` (place, commit, whether
the work tree had changes, branch, selection, `settings_hash`, the OS), has the place run the
command once, judges its report by the outcome rules, and writes one `test.result` per test
case and the `step.ended`. A place that refuses ends the run `refused` with its code, one that
vanishes mid-run `lost` `place_vanished`: neither is ever `failed`. A run whose writer dies
between the two events stays a live run in the store until its bound and a grace have passed.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orch_it.events.ids import live_id
from orch_it.events.store import Store
from orch_it.testruns import environment, outcome, place_rule
from orch_it.testruns.config import TestSettings
from orch_it.testruns.machine import MachinePlace
from orch_it.testruns.places import OwnPlace, PlaceEnd, PlaceRefused, PlaceVanished, Request

GIT_TIMEOUT_SECONDS = 30
WAIT_POLL_SECONDS = 15
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEED_OSES = ("any", "linux", "windows", "macos")
GITHUB_OSES = ("linux", "windows")  # the jobs of the `tests` workflow
RUN_ERROR_CODES = ("ids_file_unreadable", "unknown_place", "bad_work_tree", "bad_os")


class RunError(Exception):
    """A run that cannot start as asked. `code` is one of `RUN_ERROR_CODES`."""

    def __init__(self, code: str, detail: str = ""):
        if code not in RUN_ERROR_CODES:
            raise ValueError(f"not a run error code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class RunDone:
    run: str
    place: str
    outcome: str
    reason: str | None
    collected: int
    failed: int
    missing_ids: tuple[str, ...] = ()


def read_ids(path: Path) -> list[str]:
    """The ids of an ids file: one per line, surrounding blanks cut, blank and `#` lines
    skipped; an id keeps its inner spaces, quotes and brackets."""

    try:
        text = Path(path).read_text("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise RunError("ids_file_unreadable", f"{path}: {e}") from None
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def git_output(work_tree: Path | str, *args: str) -> str | None:
    """The stripped output of `git -C <work_tree> <args>`, or None when git cannot answer (no
    git, not a work tree, no commit yet, a timeout): the fact is then unknown, not an error."""

    try:
        done = subprocess.run(["git", "-C", str(work_tree), *args], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_SECONDS,
                              creationflags=NO_WINDOW)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return (done.stdout or "").strip() if done.returncode == 0 else None


def _branch(work_tree: Path) -> str | None:
    """The checked-out branch; None on a detached HEAD or outside git."""

    name = git_output(work_tree, "rev-parse", "--abbrev-ref", "HEAD")
    return None if name in (None, "", "HEAD") else name


def selection_of(ids: list[str] | None) -> str:
    """`full`, or `ids:<count>:<hash of the sorted ids>`: runs of one selection compare."""

    if ids is None:
        return "full"
    digest = hashlib.sha256("\n".join(sorted(set(ids))).encode("utf-8")).hexdigest()[:12]
    return f"ids:{len(set(ids))}:{digest}"


def offers_of(settings: TestSettings, places: dict) -> list[place_rule.Offer]:
    """Every test place in the rule's tie order: own, the machines in settings order, github.
    A place with no adapter here is offered switched off."""

    own = places.get("own")
    offers = [place_rule.Offer("own", (own.os if own else environment.this_os(),), own is not None,
                               settings.own_capacity)]
    offers += [place_rule.Offer(m.place, (m.os,), m.on and m.place in places, m.capacity)
               for m in settings.machines]
    offers.append(place_rule.Offer("github", GITHUB_OSES, settings.github_tests and "github" in places,
                                   settings.github_capacity))
    return offers


def default_places(settings: TestSettings, work_tree: Path, *, ssh: tuple[str, ...] = ("ssh",)) -> dict:
    """The own place and one machine place per machine of the settings. A machine clones and
    fetches from its own `origin` setting, else from the work tree's `origin`."""

    places = {"own": OwnPlace()}
    if settings.machines:
        origin = git_output(work_tree, "remote", "get-url", "origin")
        for machine in settings.machines:
            places[machine.place] = MachinePlace(machine, origin_url=origin, ssh=ssh)
    return places


def run_tests(store: Store, *, work_tree: Path, repo: str, settings: TestSettings,
              ids_file: Path | None = None, place: str | None = None, need_os: str = "any",
              role: str = "script", ticket: int | None = None, places: dict | None = None,
              wait_minutes: float | None = None, sleep=time.sleep, now=None) -> RunDone:
    """One run of the repo's test command at the place the rule picks (or `place`, forced).
    `places` maps a place id to its adapter (default: `default_places`). A place lost or refusing
    mid-run is left out and the rule picks again, until a place finishes the run or none is
    left. With no place left and one of them busy, the run waits on `test_place` up to
    `wait_minutes` (default: the bound), re-deciding every `WAIT_POLL_SECONDS`; giving up, or no
    busy place to wait for, ends it `refused`."""

    work_tree = Path(work_tree)
    if not work_tree.is_dir():
        raise RunError("bad_work_tree", str(work_tree))
    if need_os not in NEED_OSES:
        raise RunError("bad_os", need_os)
    ids = read_ids(ids_file) if ids_file is not None else None
    places = places if places is not None else default_places(settings, work_tree)
    if place is not None and place not in places:
        raise RunError("unknown_place", place)
    now = now or (lambda: datetime.now(UTC))
    wait_minutes = settings.bound_minutes if wait_minutes is None else wait_minutes
    run = _Run(store, work_tree, repo, settings, ids, role, ticket)
    offers = offers_of(settings, places)
    excluded: dict[str, str] = {}
    done = None
    with store.writer() as writer:
        while True:
            decision = run.decide(offers, need_os, excluded, now())
            if place is not None:
                decision = place_rule.Decision(place, "forced", decision.offered)
            elif decision.chosen is None:
                if any(o.dropped == "busy" for o in decision.offered):  # only a busy place frees up
                    decision = run.wait(writer, offers, need_os, excluded, now, wait_minutes, sleep)
                if decision.chosen is None:
                    # after a lost or refused attempt, that attempt is the run's end
                    return done or run.refused(writer, decision)
            done = run.attempt(writer, places[decision.chosen], decision)
            if place is not None or done.outcome not in ("lost", "refused"):
                return done
            excluded[done.place] = done.reason


class _Run:
    """The facts every attempt of one run shares, and the events of one attempt."""

    def __init__(self, store, work_tree, repo, settings, ids, role, ticket):
        self.store, self.work_tree, self.repo, self.settings = store, work_tree, repo, settings
        self.ids, self.selection = ids, selection_of(ids)
        self.base = {"repo": repo, "ticket": ticket, "role": role, "session": None,
                     "parent_session": None, "turn": None, "note": None}
        self.commit = git_output(work_tree, "rev-parse", "HEAD")
        status = git_output(work_tree, "status", "--porcelain")
        self.dirty = None if status is None else bool(status)
        self.branch = _branch(work_tree)

    def decide(self, offers, need_os, excluded, at) -> place_rule.Decision:
        live, measured = place_rule.read_facts(self.store, repo=self.repo, selection=self.selection,
                                               need_os=need_os, now=at)
        return place_rule.choose(need_os, offers, live, measured, excluded)

    def wait(self, writer, offers, need_os, excluded, now, wait_minutes, sleep) -> place_rule.Decision:
        envelope = self.base | {"step": "wait", "run": live_id()}
        writer.write(envelope | {"kind": "step.started",
                                 "detail": {"reason": "test_place", "on": f"test_run:{self.selection}"}})
        deadline = now() + timedelta(minutes=wait_minutes)
        while True:
            decision = self.decide(offers, need_os, excluded, now())
            if decision.chosen is not None or now() >= deadline:
                break
            sleep(WAIT_POLL_SECONDS)
        writer.write(envelope | {"kind": "step.ended", "detail": {
            "outcome": "passed" if decision.chosen else "no_result", "reason": None}})
        return decision

    def _started(self, writer, envelope, decision, run_os) -> None:
        writer.write(envelope | {"kind": "step.started", "detail": {
            "place": decision.chosen, "chosen": decision.chosen, "reason": decision.reason,
            "places_offered": decision.detail(), "selection": self.selection,
            "commit": self.commit, "dirty": self.dirty, "branch": self.branch, "pr": None,
            "workers": None, "part": None, "run_os": run_os,
            "settings_hash": environment.settings_hash(self.settings.command,
                                                       self.settings.bound_minutes,
                                                       self.settings.test_env),
            "bound_minutes": self.settings.bound_minutes}})

    def refused(self, writer, decision) -> RunDone:
        """No place took the run after the wait: one `refused` run, its reason the drop most
        likely to clear (busy, then switched off, then the OS)."""

        drops = {o.dropped for o in decision.offered}
        reason = next((code for code in ("busy", "place_off", "os_missing", "place_vanished") if code in drops),
                      "place_off")
        envelope = self.base | {"step": "test_run", "run": live_id()}
        self._started(writer, envelope, decision, None)
        return _unfinished(writer, envelope, None, "refused", reason)

    def attempt(self, writer, adapter, decision) -> RunDone:
        envelope = self.base | {"step": "test_run", "run": live_id()}
        self._started(writer, envelope, decision, adapter.os)
        request = Request(self.work_tree, self.commit, self.settings.command, self.settings.test_env,
                          self.settings.bound_minutes * 60, self.ids)
        with tempfile.TemporaryDirectory(prefix="orch-it-run-") as scratch:
            try:
                place_end = adapter.run(request, Path(scratch))
            except PlaceRefused as e:
                return _unfinished(writer, envelope, decision.chosen, "refused", e.code)
            except PlaceVanished:
                return _unfinished(writer, envelope, decision.chosen, "lost", "place_vanished")
            except BaseException:  # stopped by a person (Ctrl-C) or a crash: never left live
                _unfinished(writer, envelope, decision.chosen, "stopped", None)
                raise
            run_end = outcome.judge(place_end.report, requested_ids=self.ids, cut=place_end.cut,
                                    slept=place_end.slept)
            for detail in run_end.result_details():
                writer.write(envelope | {"kind": "test.result", "detail": detail})
        writer.write(envelope | {"kind": "step.ended",
                                 "detail": run_end.ended_detail() | _timing(place_end, run_end)})
        return RunDone(envelope["run"], decision.chosen, run_end.outcome, run_end.reason,
                       run_end.collected, run_end.failed, run_end.missing_ids)


def _unfinished(writer, envelope: dict, place: str | None, run_outcome: str, reason: str) -> RunDone:
    writer.write(envelope | {"kind": "step.ended", "detail": {
        "outcome": run_outcome, "reason": reason, "collected": 0, "passed": 0, "failed": 0,
        "skipped": 0, "summary_seen": False}})
    return RunDone(envelope["run"], place, run_outcome, reason, 0, 0)


def _timing(place_end: PlaceEnd, run_end: outcome.RunEnd) -> dict:
    detail = {"start_minutes": round(place_end.start_seconds / 60, 4),
              "run_minutes": round(place_end.run_seconds / 60, 4), "rc": place_end.rc}
    if run_end.outcome == "no_result":
        detail["log_tail"] = place_end.log_tail
    if run_end.missing_ids:
        detail["missing_ids"] = list(run_end.missing_ids[:50])
    return detail
