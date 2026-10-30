"""GitHub minutes used and left (#406 decision 4): the one reader of the account's Actions minutes in the current UTC
month, and the point `github.minutes` {used, allowance, left, source} it writes.

`used` is GitHub's own figure when the token can read the account's billing (`source: github`), else the sum of the
month's ended GitHub `test_run`s in the store (`source: store`: the layer's runs only, said so). The month is the
UTC calendar month: `used` starts at 0 on the 1st at 00:00 UTC, a run counts in the month it ENDED, and the PC's own
time zone plays no part (`now` may carry any offset; it is read as the instant it names, a naive one as UTC).

The store sum counts a private repo's runs only (a public repo's bill nothing), every repo of the account, each run
once at `charged` minutes: the stored `billed_minutes` is GitHub's billable duration of the job as the importer reads it
from the run's timing (no multiplier), and GitHub's OS multiplier (the setting `github_minute_multipliers`: a Windows
job counts twice, a macOS job ten times, by default) is applied here, once, at the read. GitHub's own `used` already has it.

`MinutesReader.poll` is the only writer of the point. It goes through the one pace of the busy reader
(`github_busy.pace_allows`: `github_poll_seconds`, the `github_reads_reserve` back-off, nothing while the hourly count is
spent), so there is no second budget; with no earlier reading, or an earlier one of another month, the pace never holds.
The billing route is GitHub's classic `users/<owner>/settings/billing/actions` (`total_minutes_used`); UNMEASURED live
(an organization has another route, and a token may lack the scope): a reading that is missing or has no whole number is
unreadable, and the store answers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from time import monotonic
from typing import NamedTuple

from orch_it.events import index
from orch_it.events.store import Store
from orch_it.testruns.config import TestSettings
from orch_it.testruns.github import Gh, GithubError, json_values, run_gh
from orch_it.testruns.github_busy import ROLE, pace_allows

KIND = "github.minutes"
SOURCE_GITHUB, SOURCE_STORE = "github", "store"
PRIVATE_TTL_SECONDS = 3600.0  # a repo's privacy is asked of GitHub again after this long; a flip shows within the hour
# (caller, repo in lower case) -> (monotonic time of the read, whether private): one answer per caller per repo
_PRIVATE: dict[tuple[Gh, str], tuple[float, bool]] = {}


class Minutes(NamedTuple):
    """The month's minutes: `used` of `allowance`, `left` of it (never below 0), and where `used` came from."""

    used: float
    allowance: int
    left: float
    source: str


def charged(billed_minutes: float, run_os: str | None, multipliers: dict[str, int]) -> float:
    """The minutes a job counts against the account: its billed minutes times its OS's factor in `multipliers` (the
    setting `github_minute_multipliers`; an OS it does not name counts as it is). Applied once, to a stored
    `billed_minutes` (which has none)."""

    return billed_minutes * multipliers.get(run_os or "", 1)


def utc(moment: datetime) -> datetime:
    """`moment` as the instant it names, in UTC; a naive one is read as UTC."""

    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def month_of(moment: datetime) -> tuple[int, int]:
    moment = utc(moment)
    return moment.year, moment.month


def next_month_start(moment: datetime) -> date:
    """The 1st of the month after `moment`'s UTC month: the day the minutes start again at 0."""

    year, month = month_of(moment)
    return date(year + (month == 12), month % 12 + 1, 1)


def _month_bounds(now: datetime) -> tuple[str, str]:
    """The store's spelling of the instants a UTC month starts and ends at (`index.utc_at`)."""

    year, month = month_of(now)
    end = next_month_start(now)
    return (index.utc_at(datetime(year, month, 1, tzinfo=UTC).isoformat()),
            index.utc_at(datetime(end.year, end.month, 1, tzinfo=UTC).isoformat()))


def write_point(store: Store, repo: str, minutes: Minutes, now: datetime) -> None:
    event = {
        "kind": KIND, "repo": repo, "at": utc(now).isoformat(), "ticket": None, "role": ROLE, "session": None,
        "parent_session": None, "turn": None, "step": None, "run": None, "note": None,
        "detail": {"used": minutes.used, "allowance": minutes.allowance, "left": minutes.left,
                   "source": minutes.source},
    }
    with store.writer() as writer:
        writer.write(event)


class MinutesReader:
    """The one paced reader of the month's GitHub minutes for `repo`'s account. `last` is the reading of its last read
    and the time it was taken."""

    def __init__(self, repo: str, store: Store, settings: TestSettings, gh: Gh = run_gh):
        self.repo, self.store, self.settings, self.gh = repo, store, settings, gh
        self.last: tuple[datetime, Minutes] | None = None

    def poll(self, now: datetime) -> Minutes | None:
        """The month's minutes read now and written as the point, or None when the pace holds a read (the last reading of
        this reader, if it is of this month, stands). Never raises for GitHub: an unreadable billing is the store's."""

        now = utc(now)
        if self.last is not None and month_of(self.last[0]) != month_of(now):
            self.last = None  # a reading of another month stands for nothing
        if not pace_allows(self.last[0] if self.last else None, now, self.settings, self.gh):
            return None
        used = self._billing()
        source = SOURCE_GITHUB
        if used is None:
            used, source = self._store_sum(now), SOURCE_STORE
        allowance = self.settings.github_minutes_allowance
        minutes = Minutes(used, allowance, max(allowance - used, 0), source)
        write_point(self.store, self.repo, minutes, now)
        self.last = (now, minutes)
        return minutes

    def current(self, now: datetime) -> Minutes:
        """The month's minutes: a fresh read the pace allows, else this month's last reading."""

        return self.poll(now) or self.last[1]

    def _owner(self) -> str:
        return self.repo.split("/")[0]

    def _billing(self) -> float | None:
        """GitHub's own `used` for the account (classic billing: `total_minutes_used`, the OS multiplier in it), or None
        when it cannot be read as a whole non-negative number."""

        try:
            found = _field(self.gh(["api", f"users/{self._owner()}/settings/billing/actions"]), "total_minutes_used")
        except GithubError:
            return None
        if isinstance(found, bool) or not isinstance(found, (int, float)) or found < 0:
            return None
        return found

    def _store_sum(self, now: datetime) -> float:
        """The month's `charged` minutes of the account's private repos' ended GitHub runs in the store, one count per
        run, the month being the UTC month of the run's end."""

        start, end = _month_bounds(now)
        owner = self._owner().lower()
        total, seen = 0.0, set()
        for repo, run, run_os, billed in self.store.query(
                "SELECT e.repo, e.run, s.run_os, e.billed_minutes FROM events e JOIN events s"
                " ON s.repo = e.repo AND s.run = e.run AND s.kind = 'step.started' AND s.step = 'test_run'"
                " WHERE e.kind = 'step.ended' AND e.step = 'test_run' AND s.place = 'github'"
                " AND e.at >= ? AND e.at < ? AND e.billed_minutes IS NOT NULL ORDER BY e.at, e.id",
                (start, end)).rows:
            if (repo, run) in seen or repo.split("/")[0].lower() != owner:
                continue
            seen.add((repo, run))
            if isinstance(billed, (int, float)) and not isinstance(billed, bool) and self._is_private(repo):
                total += charged(billed, run_os, self.settings.github_minute_multipliers)
        return total

    def _is_private(self, repo: str) -> bool:
        """Whether `repo` is private (the repo's `private` flag), asked of GitHub through this reader's caller (`gh`,
        counted by `run_gh`) and kept for `PRIVATE_TTL_SECONDS` per caller, so every reader and place of the process
        reads one repo once an hour. A repo that cannot be read counts as private, so its minutes are not left out on a
        guess."""

        key = (self.gh, repo.lower())
        known = _PRIVATE.get(key)
        if known is not None and monotonic() - known[0] < PRIVATE_TTL_SECONDS:
            return known[1]
        try:
            found = _field(self.gh(["api", f"repos/{repo}"]), "private")
        except GithubError:
            found = None
        private = found is not False
        _PRIVATE[key] = (monotonic(), private)
        return private


def _field(answer: str, key: str) -> object:
    """The `key` field of the first JSON object `gh` printed, None when there is none or the first value is no object."""

    values = json_values(answer)
    first = values[0] if values else None
    return first.get(key) if isinstance(first, dict) else None
