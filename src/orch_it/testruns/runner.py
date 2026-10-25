"""One test run, start to end, written to the store (#394 decisions 1-5).

`run_tests` reads the ids file, writes the `test_run` `step.started` (place, commit, branch,
selection, `settings_hash`, the OS), has the place run the command once, judges its report by
the outcome rules, and writes one `test.result` per test case and the `step.ended`. A run whose
writer dies between the two events stays a live run in the store until its bound and a grace
have passed.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from orch_it.events.ids import live_id
from orch_it.events.store import Store
from orch_it.testruns import environment, outcome
from orch_it.testruns.config import TestSettings
from orch_it.testruns.places import OwnPlace, PlaceEnd, Request

GIT_TIMEOUT_SECONDS = 30
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RUN_ERROR_CODES = ("ids_file_unreadable", "unknown_place", "bad_work_tree")


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


def _git(work_tree: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(work_tree), *args], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_SECONDS, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def selection_of(ids: list[str] | None) -> str:
    """`full`, or `ids:<count>:<hash of the sorted ids>`: runs of one selection compare."""

    if ids is None:
        return "full"
    digest = hashlib.sha256("\n".join(sorted(set(ids))).encode("utf-8")).hexdigest()[:12]
    return f"ids:{len(set(ids))}:{digest}"


def run_tests(store: Store, *, work_tree: Path, repo: str, settings: TestSettings,
              ids_file: Path | None = None, place: str | None = None, role: str = "script",
              ticket: int | None = None) -> RunDone:
    work_tree = Path(work_tree)
    if not work_tree.is_dir():
        raise RunError("bad_work_tree", str(work_tree))
    ids = read_ids(ids_file) if ids_file is not None else None
    places = {"own": OwnPlace()}
    chosen = place or "own"
    if chosen not in places:
        raise RunError("unknown_place", chosen)
    at = places[chosen]
    run = live_id()
    envelope = {"repo": repo, "ticket": ticket, "role": role, "session": None,
                "parent_session": None, "turn": None, "step": "test_run", "run": run,
                "note": None}
    commit = _git(work_tree, "rev-parse", "HEAD")
    request = Request(work_tree, commit, settings.command, settings.test_env,
                      settings.bound_minutes * 60, ids)
    with store.writer() as writer:
        writer.write(envelope | {"kind": "step.started", "detail": {
            "place": chosen, "chosen": chosen, "reason": "forced" if place else None,
            "selection": selection_of(ids), "commit": commit,
            "branch": _git(work_tree, "rev-parse", "--abbrev-ref", "HEAD"), "pr": None,
            "workers": None, "part": None, "run_os": at.os,
            "settings_hash": environment.settings_hash(settings.command, settings.bound_minutes,
                                                       settings.test_env),
            "bound_minutes": settings.bound_minutes}})
        with tempfile.TemporaryDirectory(prefix="orch-it-run-") as scratch:
            ended = at.run(request, Path(scratch))
            end = outcome.judge(ended.report, requested_ids=ids, cut=ended.cut, slept=ended.slept)
            for detail in end.result_details():
                writer.write(envelope | {"kind": "test.result", "detail": detail})
        writer.write(envelope | {"kind": "step.ended", "detail": end.ended_detail() | _timing(ended, end)})
    return RunDone(run, chosen, end.outcome, end.reason, end.collected, end.failed, end.missing_ids)


def _timing(ended: PlaceEnd, end: outcome.RunEnd) -> dict:
    detail = {"start_minutes": round(ended.start_seconds / 60, 4),
              "run_minutes": round(ended.run_seconds / 60, 4), "rc": ended.rc}
    if end.outcome == "no_result":
        detail["log_tail"] = ended.log_tail
    if end.missing_ids:
        detail["missing_ids"] = list(end.missing_ids[:50])
    return detail
