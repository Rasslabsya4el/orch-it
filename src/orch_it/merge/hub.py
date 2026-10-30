"""What the merge flow needs of GitHub (#400), in its own terms: the one `gh` seam.

`PullHub` is the tickets' `Hub` (#398) plus pull requests, their check runs on one head sha, their
notes, the runs of the `tests` workflow and the issues of red main. `GhPullHub` answers it through
`gh` for a real repo; the tests answer it from memory (`tests/fake_github.py`). As in the `Hub`, a
call that fails raises `orch_it.testruns.github.GithubError` and a thing that is not there is
`None`, never an error.

Every check result is read for one head sha (`checks`): a run of an older head is never read as
the result of the newer one (MERGE-12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from orch_it.merge.workflow_watch import WatchIssue, WorkflowRun
from orch_it.tickets.hub import Comment, Hub, Issue

PENDING, PASSED, FAILED, NO_RESULT = "pending", "passed", "failed", "no_result"


class LandingRefused(Exception):
    """The base's own rules refused a batch landing (branch protection, a required check, a rule that wants a pull
    request): `reason` is GitHub's message. Not a failed call: the line goes on one pull request at a time."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Pull:
    repo: str
    number: int
    title: str
    body: str
    branch: str  # the head branch
    head_sha: str
    base: str  # the base branch
    state: str  # `open`, `merged` or `closed` (closed unmerged)
    draft: bool
    mergeable: str  # `mergeable`, `conflicting` or `unknown` (GitHub has not computed it yet)
    behind: int | None  # commits of the base the head does not hold; None when unknown
    created_at: datetime
    merged_at: datetime | None = None
    merge_sha: str | None = None
    closed_at: datetime | None = None
    head_repo: str | None = None  # the repo of the head branch: a fork's is not this repo; None when deleted


@dataclass(frozen=True)
class Check:
    """One check run on a head sha, the latest of its name."""

    name: str
    state: str  # PENDING, PASSED, FAILED or NO_RESULT (cancelled, timed out, skipped, stale, ...)
    run_id: str | None  # the GitHub Actions run it belongs to
    attempt: int  # the run's attempt, 1 for the first
    started_at: datetime | None


@dataclass(frozen=True)
class MainRun:
    """One run of the `tests` workflow on a branch (a push or the nightly run)."""

    run_id: str
    sha: str
    event: str
    attempt: int
    created_at: datetime
    jobs: dict[str, str]  # required job name -> PENDING, PASSED, FAILED or NO_RESULT


@dataclass(frozen=True)
class Result:
    """One test case of a run's JUnit reports: the job whose report holds it, its id, its outcome (`passed`, `failed`,
    `error` or `skipped`) and whether it carries the `os_touching` marker."""

    job: str
    test_id: str
    outcome: str
    marked: bool


class PullHub(Hub, Protocol):
    def pull(self, repo: str, number: int) -> Pull | None: ...

    def rate_limit(self) -> tuple[int, int] | None:
        """The account's core request budget GitHub reports now: (remaining, limit); None when it cannot be read
        (a poller then keeps its interval, #414 decision 9)."""

    def open_pulls(self, repo: str, base: str) -> list[Pull]:
        """Every open pull request into `base`, drafts and forks too, oldest first."""

    def checks(self, repo: str, sha: str, *, statuses: bool = False) -> list[Check]:
        """The latest check run of each name on `sha`; with `statuses` also the commit statuses posted under a name
        (a check run wins over a status of its name). The status read is one more request: only a caller that waits
        for names a place posted as statuses (the merge line) asks for it."""

    def post_status(self, repo: str, sha: str, name: str, state: str, description: str) -> None:
        """Post a commit status named `name` on `sha`, `state` PASSED, FAILED or PENDING; `checks` reads it back. A
        status and not a check run: a user token cannot create a check run (#414 decision 6)."""

    def pull_comments(self, repo: str, number: int) -> list[Comment]:
        """What was said on a pull request, oldest first: its comments, its reviews with a body
        or asking for changes, and its review comments."""

    def create_pull(self, repo: str, base: str, branch: str, title: str, body: str) -> int: ...

    def close_pull(self, repo: str, number: int) -> None:
        """Close a pull request unmerged."""

    def required_checks_of(self, repo: str, base: str) -> set[str]:
        """The check names `base` requires before a pull request into it merges: classic protection and rulesets
        both; a base that protects nothing (or a plan that does not allow it) requires none, the empty set."""

    def update_branch(self, repo: str, number: int, head_sha: str) -> bool:
        """Bring the head up to date with its base by a merge commit, only from `head_sha`;
        False when the two conflict (nothing changed); a head that moved from `head_sha` raises."""

    def merge(self, repo: str, number: int, head_sha: str, method: str) -> str | None:
        """Merge only if the head is still `head_sha`; the merge commit's sha, None when GitHub
        refused (the head moved, the base moved under strict protection, a check is not green)."""

    def start_batch(self, repo: str, base: str, branch: str) -> str:
        """Make `branch` at the tip of `base`; the tip's sha. The merge line's batch branch (#414 item 5)."""

    def add_to_batch(self, repo: str, branch: str, head_sha: str) -> str | None:
        """Merge the commit `head_sha` into `branch` with a merge commit; the branch's new tip, or None when the two
        conflict (nothing changed)."""

    def advance_base(self, repo: str, base: str, sha: str) -> bool:
        """Move `base` forward to `sha`, never otherwise: False when `base` is not an ancestor of `sha` (it moved since
        the batch was made); the pull requests whose heads `sha` holds end merged. The base's own rules refusing the
        update raise `LandingRefused`."""

    def end_batch(self, repo: str, branch: str) -> None:
        """Delete a batch branch; one already gone is not an error."""

    def batch_branches(self, repo: str) -> list[str]:
        """The names of the repo's batch branches (`orch-batch/...`), as GitHub lists them now."""

    def rerun(self, repo: str, run_id: str) -> None:
        """Run the failed and cancelled jobs of an Actions run again."""

    def cancel(self, repo: str, run_id: str) -> None: ...

    def conflict_files(self, repo: str, base: str, number: int) -> list[str]:
        """The files whose merge into `base` conflicts, sorted; empty when it merges clean."""

    def failing_ids(self, repo: str, run_id: str) -> list[str] | None:
        """The ids of the failed tests of an Actions run, from the JUnit reports its jobs upload;
        None when no report can be read."""

    def results(self, repo: str, run_id: str) -> list[Result] | None:
        """Every test case of the latest attempt of each job of an Actions run, from the JUnit reports its jobs
        upload, one `Result` per case; None when no report can be read."""

    def default_branch(self, repo: str) -> str:
        """The repo's default branch as GitHub reports it."""

    def branch_runs(self, repo: str, branch: str) -> list[MainRun]:
        """The runs of the `tests` workflow on `branch` from a push or the schedule, newest first."""

    def pulls_of_commit(self, repo: str, sha: str) -> list[int]:
        """The merged pull requests that brought `sha` to its branch."""

    def issues_with_label(self, repo: str, label: str) -> list[int]:
        """The numbers of the open issues (no pull requests) carrying `label`."""

    def open_issues(self, repo: str) -> list[Issue]:
        """Every open issue of the repo whatever its labels, pull requests left out."""

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> int: ...

    def close_issue(self, repo: str, number: int, reason: str) -> None:
        """Close an issue as `completed` or `not_planned`."""

    def latest_workflow_runs(self, repo: str) -> list[WorkflowRun]:
        """The latest completed run of each workflow GitHub lists; a workflow with none is left out."""

    def workflow_files(self, repo: str) -> list[str]:
        """The file path of every workflow GitHub lists, in the order it lists them."""

    def watch_issues(self, repo: str) -> list[WatchIssue]:
        """The open issues (no pull requests) that are workflow watch tickets, whatever their labels, with the
        run each last named."""

    def actions_enabled(self, repo: str) -> bool: ...

    def event_artifacts(self, repo: str) -> list[tuple[str, str]]:
        """(run id, name) of every artifact of the repo's workflows named `orch-it-events-*` that
        has not expired: the events a workflow's run wrote in its own store."""

    def download_artifact(self, repo: str, run_id: str, name: str, folder: Path) -> None:
        """Unpack one artifact of a run into `folder`."""
