"""The suite the merge line runs on a batch (#414 item 5): one required test run on one commit, placed by the place rule.

`place_rule_suite` is that run: it checks the commit out into a folder of its own, hands the folder to `run_tests` (so the
run is a `test_run` in the store, at the place the rule picks, and the passes of a line are those runs) and answers what
the line needs, `passed`, `failed` with the ids of the failing tests, or `no_result`. A run that was refused, lost,
cut or wrote no readable report is `no_result`, never `failed`. `git_checkout` is the checkout for a real repo: the
commit is fetched from `origin` into the work tree and made a detached work tree of its own, removed after the run.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from orch_it.events.store import Store
from orch_it.merge import git as merge_git
from orch_it.testruns.config import TestSettings
from orch_it.testruns.runner import run_tests
from orch_it.tickets.park import ROLE

PASSED, FAILED, NO_RESULT = "passed", "failed", "no_result"


@dataclass(frozen=True)
class SuiteResult:
    outcome: str  # PASSED, FAILED or NO_RESULT
    failing: tuple[str, ...] = ()  # the ids of the failing tests, when FAILED


Suite = Callable[[str], SuiteResult]  # the commit's sha -> what the required suite said of its tree
Checkout = Callable[[str], AbstractContextManager[Path]]  # the commit's sha -> a folder holding its tree


def place_rule_suite(store: Store, repo: str, settings: TestSettings, checkout: Checkout, *, role: str = ROLE,
                     places: dict | None = None) -> Suite:
    """The suite of a commit as one `run_tests` call: the full selection, any OS, at the place the rule picks
    (`places`: the adapters, default those of `settings`)."""

    def suite(sha: str) -> SuiteResult:
        with checkout(sha) as tree:
            done = run_tests(store, work_tree=tree, repo=repo, settings=settings, role=role, places=places)
        if done.outcome == "passed":
            return SuiteResult(PASSED)
        if done.outcome != "failed":
            return SuiteResult(NO_RESULT)
        failing = store.query("SELECT test_id FROM events WHERE kind = 'test.result' AND run = ?"
                              " AND outcome IN ('failed', 'error') ORDER BY test_id", (done.run,)).rows
        return SuiteResult(FAILED, tuple(dict.fromkeys(test_id for (test_id,) in failing)))

    return suite


def git_checkout(work_tree: Path, remote: str = "origin") -> Checkout:
    """The checkout of a real repo: `sha` fetched from `remote` into `work_tree`'s object store, then a detached work
    tree of it in a temporary folder, removed when the run is done. `merge_git.GitError` when git fails."""

    git = merge_git.work_tree(Path(work_tree))

    @contextmanager
    def checkout(sha: str):
        git(["fetch", "--quiet", "--no-tags", remote, sha])
        with tempfile.TemporaryDirectory(prefix="orch-it-batch-") as scratch:
            tree = Path(scratch) / "tree"
            git(["worktree", "add", "--quiet", "--detach", str(tree), sha])
            try:
                yield tree
            finally:
                git(["worktree", "remove", "--force", str(tree)])

    return checkout
