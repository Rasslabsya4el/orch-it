"""The paced reader of GitHub's account-wide lists (#406 decision 3, row 3, row 11 as fixtures): one point `github.busy`
{running, queued, capacity} per read, at most once per `github_poll_seconds`, backing off under `github_reads_reserve`,
no read past the budget, none when the Actions budget would cross; a failed read writes no point.

Seams: `BusyReader.poll` (the one reader) and the store's `github.busy` rows read the way a view reads them."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fake_github import FakeActions

from orch_it.events import github_budget
from orch_it.events.store import Store
from orch_it.testruns.config import TestSettings
from orch_it.testruns.github import GithubError
from orch_it.testruns.github_busy import BusyReader

REPO = "acme-x/widgets-y"  # the repo under test
OTHER = "acme-x/gadgets-z"  # another repo of the same account
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def world(tmp_path):
    actions = FakeActions(tmp_path, REPO, NOW, now=NOW)
    return SimpleNamespace(actions=actions, store=Store.open(tmp_path / "store"))


def reader(world, **settings):
    return BusyReader(REPO, world.store, TestSettings(**settings), world.actions)


def busy_points(store):
    """Every stored `github.busy` point, oldest first: (running, queued, capacity)."""

    return store.query("SELECT running, queued, capacity FROM events WHERE kind = 'github.busy' ORDER BY at").rows


def latest_as_a_view_reads_it(store):
    """The newest point as a view reads the store: (running, capacity)."""

    rows = store.query("SELECT running, capacity FROM events WHERE kind = 'github.busy' ORDER BY at DESC LIMIT 1").rows
    return rows[0] if rows else None


def account_lists_read(world, since):
    """Whether the reader asked GitHub for the account's repos after call number `since`."""

    return any("user/repos" in " ".join(call) for call in world.actions.calls[since:])


def test_three_runs_in_progress_one_by_hand_and_one_queued_make_one_point_of_3_and_1(world):
    world.actions.hold_jobs(OTHER, ["ubuntu-latest"], event="push")  # started by the layer
    world.actions.hold_jobs(OTHER, ["ubuntu-latest"], event="push")  # started by the layer
    world.actions.hold_jobs(OTHER, ["windows-latest"], event="workflow_dispatch")  # started by hand on github.com
    world.actions.hold_jobs(OTHER, ["ubuntu-latest"], status="queued", queued_for=2, event="push")

    load = reader(world).poll(NOW)

    assert (load.running, load.queued) == (3, 1)
    assert busy_points(world.store) == [(3, 1, 20)]  # capacity: the setting github_capacity, default 20


def test_under_the_reserve_the_next_read_waits_longer_and_none_past_the_budget(world):
    world.actions.hold_jobs(OTHER, ["ubuntu-latest"])
    busy = reader(world)
    assert busy.poll(NOW) is not None  # a full budget: read at once

    world.actions.core = (900, 5000)  # under the 1000 reserve: one doubling of the 30 s base, so 60 s
    assert busy.poll(NOW + timedelta(seconds=31)) is None  # the base alone would have read here
    assert busy.poll(NOW + timedelta(seconds=61)) is not None

    world.actions.core = (0, 5000)  # the budget is spent: no read at all, however long the wait
    before = len(world.actions.calls)
    assert busy.poll(NOW + timedelta(hours=2)) is None
    assert not account_lists_read(world, before)


def test_no_read_when_the_actions_budget_of_the_repo_would_cross_its_threshold(world):
    world.actions.hold_jobs(OTHER, ["ubuntu-latest"])
    github_budget.record_budget(world.store, REPO, budget="actions", remaining=150, limit=1000, at=NOW)
    before = len(world.actions.calls)

    assert reader(world).poll(NOW) is None
    assert not account_lists_read(world, before)
    assert busy_points(world.store) == []


def test_one_point_at_most_per_github_poll_seconds(world):
    world.actions.hold_jobs(OTHER, ["ubuntu-latest"])
    busy = reader(world, github_poll_seconds=10)
    assert busy.poll(NOW) is not None
    assert busy.poll(NOW + timedelta(seconds=5)) is None
    assert busy.poll(NOW + timedelta(seconds=10)) is not None
    assert len(busy_points(world.store)) == 2


def test_the_figure_is_the_accounts_busy_of_the_limit_not_this_repos_own_jobs(world):
    world.actions.hold_jobs(OTHER, ["ubuntu-latest"], event="push")  # started by the layer in B
    world.actions.hold_jobs(OTHER, ["ubuntu-latest"], event="push")  # started by the layer in B
    world.actions.hold_jobs(OTHER, ["windows-latest"], event="workflow_dispatch")  # started by hand in B
    assert world.actions.runs[REPO] == []  # the repo under test holds 0 jobs

    reader(world, github_capacity=10).poll(NOW)

    running, capacity = latest_as_a_view_reads_it(world.store)
    assert (running, capacity) == (3, 10)  # not this repo's 0, not the default 20


def test_a_failed_read_writes_no_point_and_the_last_point_keeps_its_time(world):
    world.actions.hold_jobs(OTHER, ["ubuntu-latest"])
    reader(world).poll(NOW)
    before = busy_points(world.store)
    assert before == [(1, 0, 20)]

    def broken(args):
        raise GithubError("gh_failed", "no network")

    failing = BusyReader(REPO, world.store, TestSettings(), broken)
    with pytest.raises(GithubError):
        failing.poll(NOW + timedelta(hours=1))

    assert busy_points(world.store) == before
    assert latest_as_a_view_reads_it(world.store) == (1, 20)
