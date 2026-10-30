"""The busy GitHub machines of an account (#406 decision 3, #414 decision 11): the one paced reader of GitHub's
account-wide lists of runs in progress and queued, and the point `github.busy` {running, queued, capacity} it writes.

Every repo of the account and every trigger counts (a run started by hand on github.com, a push, a schedule): the
count is the account's, not the tested repo's. `limit_of` is the account's limit the place rule and the view both read:
the jobs running when a queued one has waited past the grace while others run, else the setting `github_capacity`.

`BusyReader.poll` is the only writer of the point and the only reader of the lists for it. Once it has an earlier reading
it reads at most once per `github_poll_seconds`, waits longer while GitHub's remaining hourly count is under
`github_reads_reserve`, and reads nothing while the remaining count is spent; with no earlier reading the pace never
holds the first read. The Actions budget of the repo holds a read only when the caller keeps that gate on (the
availability read does; the priority between repos does not). A failed read writes no point and raises (the last
point keeps its time).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import NamedTuple

from orch_it.events import github_budget
from orch_it.events.store import Store
from orch_it.testruns.config import TestSettings
from orch_it.testruns.github import Gh, GithubError, items_of, json_values, moment_of, run_gh

KIND = "github.busy"
ROLE = "github"  # the events a GitHub reading writes name the GitHub role, as the importer's do
QUEUE_GRACE_SECONDS = 120.0  # a job queued longer than this while others run shows the account's limit
READ_ESTIMATE = 40  # the requests one account read, or one availability reading, may make: asked of the Actions budget first
PER_PAGE = 100


class AccountLoad(NamedTuple):
    """The account's machines of the limit: jobs in progress, jobs queued, and the longest wait in seconds of a
    queued one (0 with none)."""

    running: int
    queued: int
    longest_wait_seconds: float


class RateLimit(NamedTuple):
    """GitHub's core request budget as it reads now: `remaining` requests left of `limit` in the hour."""

    remaining: int
    limit: int


def limit_of(load: AccountLoad, capacity: int) -> int:
    """The account's limit: the jobs running when a queued one has waited past the grace while others run, else
    `capacity` (the setting `github_capacity`). The one function the place rule and the view read."""

    return load.running if load.longest_wait_seconds >= QUEUE_GRACE_SECONDS and load.running else capacity


def account_load(gh: Gh, repo: str, now: datetime) -> AccountLoad:
    """The account of `repo`'s jobs in progress and queued on GitHub's machines, read from GitHub's own lists: every
    repo of the owner (not archived), every run in progress and queued, every job of each run. Raises `GithubError`
    when GitHub cannot be read."""

    owner = repo.split("/")[0].lower()
    listed = items_of(gh(["api", "--paginate", f"user/repos?per_page={PER_PAGE}"]), "repos")
    running, queued, longest_wait = 0, 0, 0.0
    for entry in listed:
        full = entry.get("full_name")
        if (not isinstance(full, str) or entry.get("archived")
                or str((entry.get("owner") or {}).get("login", "")).lower() != owner):
            continue
        for status in ("in_progress", "queued"):
            runs = items_of(gh(["api", "--paginate", f"repos/{full}/actions/runs?status={status}&per_page={PER_PAGE}"]),
                            "workflow_runs")
            for run in runs:
                jobs = items_of(gh(["api", "--paginate", f"repos/{full}/actions/runs/{run['id']}/jobs"
                                                f"?filter=latest&per_page={PER_PAGE}"]), "jobs")
                for job in jobs:
                    if not _is_machine_of_the_limit(job):
                        continue
                    if job.get("status") == "in_progress":
                        running += 1
                    elif job.get("status") == "queued":
                        queued += 1
                        queued_at = moment_of(job.get("created_at"))
                        if queued_at is not None:
                            longest_wait = max(longest_wait, (now - queued_at).total_seconds())
    return AccountLoad(running, queued, longest_wait)


def _is_machine_of_the_limit(job: dict) -> bool:
    """A job on a GitHub-hosted Linux or Windows runner: the machines the account's limit is about (a self-hosted
    runner or a macOS one is not counted against it)."""

    from orch_it.testruns.github import os_of

    labels = [label for label in job.get("labels") or [] if isinstance(label, str)]
    hosted = "self-hosted" not in (label.lower() for label in labels)
    os_name = os_of({"labels": labels})
    return hosted and os_name is not None and os_name != "macos"


def rate_limit(gh: Gh) -> RateLimit | None:
    """The core request budget of the token as GitHub reports it now; None when it cannot be read. Not stored by this
    module: its call names no repo, the layer's counter stores the reading."""

    try:
        found = _object(gh(["api", "rate_limit"]))
    except GithubError:
        return None
    resources = found.get("resources")
    core = resources.get("core") if isinstance(resources, dict) else None
    if not isinstance(core, dict):
        return None
    remaining, limit = core.get("remaining"), core.get("limit")
    if type(remaining) is not int or type(limit) is not int or limit <= 0:
        return None
    return RateLimit(remaining, limit)


def reserve_wait(base: float, rate: RateLimit | None, reserve: int) -> float:
    """The wait before the next account read: `base` while the remaining count is at least `reserve` (or nothing is
    known); under it the interval doubles per halving of the level, as `github_budget.poll_interval` does, with the
    reserve as its level."""

    if rate is None or reserve <= 0 or rate.limit <= 0:
        return float(base)
    return github_budget.poll_interval(base, rate.remaining, rate.limit, reserve / rate.limit)


def write_point(store: Store, repo: str, load: AccountLoad, capacity: int, now: datetime) -> None:
    """Write the point `github.busy` of a read: the jobs running and queued, and the limit it was read against."""

    event = {
        "kind": KIND, "repo": repo, "at": now.isoformat(), "ticket": None, "role": ROLE, "session": None,
        "parent_session": None, "turn": None, "step": None, "run": None, "note": None,
        "detail": {"running": load.running, "queued": load.queued, "capacity": capacity},
    }
    with store.writer() as writer:
        writer.write(event)


class BusyReader:
    """The one paced reader of the account's busy machines for `repo`'s account. `last` is the reading of its last
    successful read and the time it was taken."""

    def __init__(self, repo: str, store: Store, settings: TestSettings, gh: Gh = run_gh):
        self.repo, self.store, self.settings, self.gh = repo, store, settings, gh
        self.last: tuple[datetime, AccountLoad] | None = None

    def poll(self, now: datetime, *, budget_gate: bool = True) -> AccountLoad | None:
        """The account's load read now and written as the point, or None when no read is due. With an earlier reading
        in this instance the pace holds a read: the last one is younger than `github_poll_seconds`, the wait under the
        reserve has not passed, or the remaining count is spent. With no earlier reading the pace never holds it.
        `budget_gate` (on by default) holds the read while the Actions budget of the repo would cross its threshold;
        the priority between repos reads with it off. A failed read raises `GithubError` and writes nothing."""

        base = self.settings.github_poll_seconds
        if self.last is not None:
            if now - self.last[0] < timedelta(seconds=base):
                return None
            rate = rate_limit(self.gh)
            if now - self.last[0] < timedelta(seconds=reserve_wait(base, rate, self.settings.github_reads_reserve)):
                return None
            if rate is not None and rate.remaining <= 0:
                return None
        if budget_gate and github_budget.would_cross(self.repo, READ_ESTIMATE, store=self.store, now=now):
            return None
        load = account_load(self.gh, self.repo, now)  # raises GithubError: no point is written
        capacity = limit_of(load, self.settings.github_capacity)
        write_point(self.store, self.repo, load, capacity, now)
        self.last = (now, load)
        return load


def _object(text: str) -> dict:
    values = json_values(text)
    return values[0] if values and isinstance(values[0], dict) else {}
