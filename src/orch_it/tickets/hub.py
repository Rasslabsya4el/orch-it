"""What the ticket commands need of GitHub (#398 decisions 4 and 5), in the tickets' own terms.

`Hub` is the seam: `GhHub` answers it through `gh api` for a real repo; the tests answer it
from memory. A call that fails raises `orch_it.testruns.github.GithubError`; a thing that is
not there (an issue, a run, a file) is `None`, never an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# the hidden mark of every comment the layer writes: such a comment is never a person's (PARK-9)
LAYER_MARK = "<!-- orch-it -->"


@dataclass(frozen=True)
class Issue:
    repo: str
    number: int
    title: str
    body: str
    labels: tuple[str, ...]  # names as GitHub holds them
    open: bool
    state_reason: str | None  # of a closed issue: `completed`, `not_planned`, ...
    is_pr: bool
    merged: bool  # a pull request merged


@dataclass(frozen=True)
class Comment:
    author: str
    bot: bool  # written by a bot account (a GitHub App, `github-actions`)
    body: str
    at: datetime

    @property
    def by_a_person(self) -> bool:
        return not self.bot and LAYER_MARK not in self.body


@dataclass(frozen=True)
class RunState:
    ended: bool
    passed: bool  # ended green


class Hub(Protocol):
    def issue(self, repo: str, number: int) -> Issue | None: ...

    def parent(self, repo: str, number: int) -> tuple[str, int] | None:
        """The ticket's parent as (repo, number), from GitHub's sub-issues."""

    def parked(self, repo: str) -> list[int]:
        """The numbers of the repo's open issues carrying the `parked` label."""

    def write(self, repo: str, number: int, body: str, labels: list[str]) -> None:
        """Set the issue's body and its whole label set in one request."""

    def comment(self, repo: str, number: int, body: str) -> None: ...

    def comments(self, repo: str, number: int) -> list[Comment]: ...

    def parked_at(self, repo: str, number: int) -> datetime | None:
        """When the `parked` label was last put on the issue."""

    def run(self, repo: str, run_id: str) -> RunState | None:
        """A GitHub Actions run of the repo."""

    def layer_version(self, repo: str) -> str | None:
        """The orch-it version the repo's default branch runs, `X.Y.Z`."""
