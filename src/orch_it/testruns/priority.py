"""Priority between the repos of one account (#414 item 4): GitHub queues the account's jobs with no priority, so the
layer holds a start itself. When the account's live jobs reach its concurrency, a repo's line does not ask for a run
while a repo of the same account with a higher `github_priority` has a ready member; below the concurrency, with equal
priorities, or when a reading fails, the start is as it always was.

`holder` is the decision, pure over `Readings` (what the account's load is, and whether a repo has a ready member). It
reads nothing while no repo outranks this one, and asks for a repo's members only once the account is at its
concurrency. `HubReadings` answers the readings from GitHub: the busy count the `github` place already computes
(`GithubPlace.availability`, the account's jobs, never a constant) and each repo's merge-line members. `hold_of` is
what a line calls, or None when no repo of its account is set above it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Protocol

from orch_it.merge import marks
from orch_it.merge.hub import PullHub
from orch_it.testruns.config import github_priority
from orch_it.testruns.github import GithubError
from orch_it.testruns.places import Availability


class Readings(Protocol):
    """What the decision reads; None is a reading that failed."""

    def load(self) -> tuple[int, int] | None:
        """The account's live jobs and the limit they are counted against: (busy, limit)."""

    def has_ready(self, repo: str) -> bool | None:
        """Whether `repo`'s merge line has a ready member."""


def priorities_of(raw: dict, repo: str) -> dict[str, int]:
    """`repo` and every repo of its account the settings file names under `repos`, each with its `github_priority`
    (the repo's own spelling kept). A repo of another account is not in it; a bad value raises `SettingsError`."""

    found = {repo: github_priority(raw, repo)}  # first: it refuses a `repos` that is no object
    account = repo.split("/")[0].lower()
    named = [name for name in raw.get("repos", {}) if isinstance(name, str) and name.split("/")[0].lower() == account]
    for name in named:
        if name.lower() != repo.lower():
            found[name] = github_priority(raw, name)
    return found


def holder(repo: str, priorities: Mapping[str, int], readings: Readings) -> str | None:
    """The repo whose ready member holds `repo`'s start, or None. `priorities` as `priorities_of` gives them. A reading
    that fails never holds."""

    own = priorities[repo]
    higher = [name for name, value in priorities.items() if value > own]
    if not higher:
        return None
    load = readings.load()
    if load is None or load[0] < load[1]:
        return None
    return next((name for name in higher if readings.has_ready(name)), None)


class HubReadings:
    """`Readings` from GitHub for the account `owner`: `availability(now)` is the `github` place's, `clock` the line's."""

    def __init__(self, hub: PullHub, owner: str, availability: Callable[[datetime], Availability],
                 clock: Callable[[], datetime]):
        self.hub, self.owner, self.availability, self.clock = hub, owner, availability, clock

    def load(self) -> tuple[int, int] | None:
        found = self.availability(self.clock())
        return (found.busy, found.limit) if found.on and found.limit > 0 else None

    def has_ready(self, repo: str) -> bool | None:
        try:
            return bool(marks.ready_pulls(self.hub, repo, self.hub.default_branch(repo), self.owner))
        except GithubError:
            return None


def hold_of(raw: dict, repo: str, readings: Readings) -> Callable[[], str | None] | None:
    """The check a line makes before it asks for a run: the repo holding its start, or None. None, not a check, when no
    repo of the account is set above `repo` (the line then reads nothing for it)."""

    priorities = priorities_of(raw, repo)
    if not any(value > priorities[repo] for value in priorities.values()):
        return None
    return lambda: holder(repo, priorities, readings)
