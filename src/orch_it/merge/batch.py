"""The suite the merge line runs on a batch (#414 item 5): one required test run on one commit, placed by the place rule.

`place_rule_suite` is that run: it checks the commit out into a folder of its own, hands the folder to `run_tests` (so the
run is a `test_run` in the store, at the place the rule picks, and the passes of a line are those runs) and answers what
the line needs, `passed`, `failed` with the ids of the failing tests, or `no_result`. A run that was refused, lost,
cut or wrote no readable report is `no_result`, never `failed`. `git_checkout` is the checkout for a real repo: the
commit is fetched from `origin` into the work tree and made a detached work tree of its own, removed after the run.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from orch_it.events.store import Store
from orch_it.merge import git as merge_git
from orch_it.testruns.config import TestSettings
from orch_it.testruns.github_place import checks_on_os, os_free_checks, workflow_text_of
from orch_it.testruns.places import RUNS_NOT_STARTING
from orch_it.testruns.runner import run_tests
from orch_it.tickets.park import ROLE

PASSED, FAILED, NO_RESULT = "passed", "failed", "no_result"
BATCH_PREFIX = "orch-batch/"  # a batch branch is `orch-batch/<unix seconds>-<n>`


def branch_name(moment: datetime, n: int) -> str:
    return f"{BATCH_PREFIX}{int(moment.timestamp())}-{n}"


def stale(name: str, now: datetime, minutes: float) -> bool:
    """A batch branch older than `minutes` by the time in its name, or whose name holds none: a young one may belong to
    a line on another machine."""

    try:
        made = datetime.fromtimestamp(int(name.removeprefix(BATCH_PREFIX).split("-")[0]), UTC)
    except (ValueError, OverflowError, OSError):
        return True
    return (now - made).total_seconds() > minutes * 60


@dataclass(frozen=True)
class SuiteResult:
    outcome: str  # PASSED, FAILED or NO_RESULT
    failing: tuple[str, ...] = ()  # the ids of the failing tests, when FAILED
    github_out: str | None = None  # why GitHub's runs would not start (`places.RUNS_NOT_STARTING`), when the run went elsewhere
    covered: tuple[str, ...] = ()  # the required check names the passed run covers, set when GitHub is out: the jobs of
    # the tree's `tests` workflow that run on the OS the run went on, and the OS-free names (no job of the workflow)


class Suite(Protocol):
    """The commit's sha -> what the required suite said of its tree; `only`: run just those test ids."""

    def __call__(self, sha: str, only: Sequence[str] | None = None) -> SuiteResult: ...


Checkout = Callable[[str], AbstractContextManager[Path]]  # the commit's sha -> a folder holding its tree


def place_rule_suite(store: Store, repo: str, settings: TestSettings, checkout: Checkout, *, role: str = ROLE,
                     places: dict | None = None) -> Suite:
    """The suite of a commit as one `run_tests` call: the full selection, any OS, at the place the rule picks
    (`places`: the adapters, default those of `settings`)."""

    def suite(sha: str, only: Sequence[str] | None = None) -> SuiteResult:
        with checkout(sha) as tree, tempfile.TemporaryDirectory(prefix="orch-it-ids-") as scratch:
            ids_file = None
            if only:
                ids_file = Path(scratch) / "ids.txt"
                ids_file.write_text(chr(10).join(only) + chr(10), "utf-8")
            done = run_tests(store, work_tree=tree, repo=repo, settings=settings, role=role, places=places,
                             ids_file=ids_file)
            covered = ()
            if done.outcome == PASSED and done.github_out in RUNS_NOT_STARTING:
                text = workflow_text_of(tree)  # a pass speaks for the OS-bound names of its own OS and the OS-free ones
                covered = (*checks_on_os(text, settings.required_checks, done.run_os),
                           *os_free_checks(text, settings.required_checks))
        if done.outcome == PASSED:
            return SuiteResult(PASSED, github_out=done.github_out, covered=covered)
        if done.outcome != FAILED:
            return SuiteResult(NO_RESULT, github_out=done.github_out)
        failing = store.query("SELECT test_id FROM events WHERE kind = 'test.result' AND run = ?"
                              " AND outcome IN ('failed', 'error') ORDER BY test_id", (done.run,)).rows
        return SuiteResult(FAILED, tuple(dict.fromkeys(test_id for (test_id,) in failing)), done.github_out)

    return suite


def git_checkout(work_tree: Path, remote: str = "origin") -> Checkout:
    """The checkout of a real repo: `sha` fetched from `remote` into `work_tree`'s object store, then a detached work
    tree of it in a temporary folder, removed when the run is done. `merge_git.GitError` when git fails."""

    git = merge_git.work_tree(Path(work_tree))

    @contextmanager
    def checkout(sha: str):
        git(["fetch", "--quiet", "--no-tags", remote, sha])
        git(["worktree", "prune"])  # the work tree of a line that died, its folder gone
        with tempfile.TemporaryDirectory(prefix="orch-it-batch-") as scratch:
            tree = Path(scratch) / "tree"
            git(["worktree", "add", "--quiet", "--detach", str(tree), sha])
            try:
                yield tree
            finally:
                git(["worktree", "remove", "--force", str(tree)])

    return checkout
