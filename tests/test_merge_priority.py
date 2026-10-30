"""Priority between the repos of one account (#414 item 4, row 6): when the account's jobs are at its concurrency a
repo's line does not ask for a run while a repo of the same account with a higher `github_priority` has a ready
member. The deciding function is pure (`testruns/priority.py`); the line asks it before the place rule."""

from datetime import UTC, datetime, timedelta

import pytest
from fake_github import REPO
from line_world import line_world
from test_merge_batch import make_suite, passes

from orch_it.merge import marks
from orch_it.merge.line import run_line
from orch_it.merge.settings import MergeSettings
from orch_it.merge.hub import Pull
from orch_it.settings import SettingsError
from orch_it.testruns import priority
from orch_it.testruns.config import DEFAULT_GITHUB_PRIORITY, TestSettings, github_priority
from orch_it.testruns.github import GithubError
from orch_it.testruns.places import Availability
from orch_it.tickets.hub import LAYER_MARK, Comment

ACCOUNT = "acme-dev"
LOW, HIGH, SAME = f"{ACCOUNT}/gadgets-low", f"{ACCOUNT}/widgets-high", f"{ACCOUNT}/gizmos-same"
STRANGER = "zeta-labs/widgets-high"  # another account, never a rival
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def settings_of(**priorities):
    """A settings file with each repo's `github_priority` under `repos.<owner/name>`."""

    return {"repos": {repo: {"github_priority": value} for repo, value in priorities.items()}}


class Readings:
    """The account as the readings see it: its jobs `busy` of `limit` and the repos with a ready member; a reading of
    None (or a raise) is a failed one. `asked` keeps what was read, in order."""

    def __init__(self, busy=20, limit=20, ready=(), load_fails=False, ready_fails=()):
        self.busy, self.limit, self.ready = busy, limit, set(ready)
        self.load_fails, self.ready_fails = load_fails, set(ready_fails)
        self.asked = []

    def load(self):
        self.asked.append("load")
        return None if self.load_fails else (self.busy, self.limit)

    def has_ready(self, repo):
        self.asked.append(repo)
        return None if repo in self.ready_fails else repo in self.ready


# the setting


def test_github_priority_defaults_to_equal_and_each_repo_may_set_its_own():
    assert github_priority({}, LOW) == DEFAULT_GITHUB_PRIORITY
    raw = settings_of(**{HIGH: 5})
    assert github_priority(raw, HIGH) == 5 and github_priority(raw, LOW) == DEFAULT_GITHUB_PRIORITY
    assert github_priority(raw, HIGH.upper()) == 5  # a repo's name in any case
    assert github_priority({"github_priority": 2}, LOW) == 2  # the top level is every repo's default
    assert TestSettings.from_settings(raw, HIGH).github_priority == 5


@pytest.mark.parametrize("bad", [True, 1.5, "high", None, [1]])
def test_a_github_priority_that_is_no_whole_number_is_refused_by_name(bad):
    with pytest.raises(SettingsError) as refused:
        github_priority(settings_of(**{HIGH: bad}), HIGH)
    assert refused.value.code == "bad_setting" and "github_priority" in str(refused.value)
    with pytest.raises(SettingsError):
        TestSettings.from_settings(settings_of(**{HIGH: bad}), HIGH)


def test_priorities_of_names_the_repos_of_the_same_account_only():
    raw = settings_of(**{HIGH: 5, LOW: 1, STRANGER: 9})
    assert priority.priorities_of(raw, LOW) == {LOW: 1, HIGH: 5}
    assert priority.priorities_of(raw, LOW.upper()) == {LOW.upper(): 1, HIGH: 5}


# the deciding function


def test_the_lower_repo_is_held_by_a_higher_one_with_a_ready_member_while_the_account_is_at_concurrency():
    readings = Readings(ready={HIGH})
    assert priority.holder(LOW, {LOW: 1, HIGH: 5}, readings) == HIGH
    assert priority.holder(HIGH, {LOW: 1, HIGH: 5}, readings) is None  # the higher repo is never held by a lower one


@pytest.mark.parametrize("busy", [0, 19])
def test_below_the_concurrency_nobody_is_held(busy):
    assert priority.holder(LOW, {LOW: 1, HIGH: 5}, Readings(busy=busy, ready={HIGH})) is None


def test_above_the_concurrency_still_holds():
    assert priority.holder(LOW, {LOW: 1, HIGH: 5}, Readings(busy=23, ready={HIGH})) == HIGH


def test_a_higher_repo_with_no_ready_member_holds_nothing():
    assert priority.holder(LOW, {LOW: 1, HIGH: 5}, Readings(ready=())) is None


def test_equal_priorities_hold_nothing_and_nothing_is_read():
    readings = Readings(ready={SAME, LOW})
    assert priority.holder(LOW, {LOW: 3, SAME: 3}, readings) is None
    assert priority.holder(LOW, {LOW: 3}, readings) is None
    assert readings.asked == []  # no higher repo: no request to GitHub


def test_a_failed_reading_leaves_the_start_as_it_is():
    assert priority.holder(LOW, {LOW: 1, HIGH: 5}, Readings(ready={HIGH}, load_fails=True)) is None
    assert priority.holder(LOW, {LOW: 1, HIGH: 5}, Readings(ready={HIGH}, ready_fails={HIGH})) is None
    # one higher repo unread does not hide another that is read
    other = f"{ACCOUNT}/sprockets-top"
    held = priority.holder(LOW, {LOW: 1, HIGH: 5, other: 7}, Readings(ready={other}, ready_fails={HIGH}))
    assert held == other


def test_the_higher_repo_starts_first_in_ten_of_ten_trials_whoever_asks_first():
    """Two repos of one account at concurrency, both with a ready member: each trial asks in an order (the lower repo
    first and last alternately); a repo not held starts and its ready member is taken. The higher repo's run is the first
    to start every time."""

    firsts = []
    for trial in range(10):
        readings = Readings(ready={LOW, HIGH})
        ready, started = readings.ready, []
        asking = [LOW, HIGH] if trial % 2 == 0 else [HIGH, LOW]
        priorities = {LOW: 1, HIGH: 5}
        for _ in range(3):  # a repo held asks again at its next poll
            for repo in asking:
                if repo in ready and priority.holder(repo, priorities, readings) is None:
                    started.append(repo)
                    ready.discard(repo)
        firsts.append(started[0])
        assert started == [HIGH, LOW]  # the lower one starts once the higher one's ready member is gone
    assert firsts == [HIGH] * 10


# the readings of the real hub


class Hub:
    """The pull requests and comments of several repos, by repo; `fails` makes a repo's reading raise."""

    def __init__(self):
        self.pulls, self.notes, self.fails = {}, {}, set()

    def add(self, repo, number, *, ready=True, draft=False, head_repo=None, left=False):
        pull = Pull(repo, number, "A change", "", f"b{number}", f"s{number}", "main", "open", draft, "mergeable", 0, NOW,
                    head_repo=head_repo or repo)
        self.pulls.setdefault(repo, []).append(pull)
        notes = []
        if ready:
            notes.append(Comment("acme-dev", False, marks.ready_comment(None, f"s{number}", NOW), NOW))
        if left:
            notes.append(Comment("acme-dev", False, marks.left_comment("red", "red"), NOW + timedelta(minutes=1)))
        self.notes[(repo, number)] = notes

    def default_branch(self, repo):
        if repo in self.fails:
            raise GithubError("gh_failed", repo)
        return "main"

    def open_pulls(self, repo, base):
        assert base == "main"
        return self.pulls.get(repo, [])

    def pull_comments(self, repo, number):
        return self.notes[(repo, number)]


def hub_readings(hub, availability=Availability(True, 20, 20, "20 busy of 20")):
    return priority.HubReadings(hub, ACCOUNT, lambda now: availability, lambda: NOW)


def test_a_repo_has_a_ready_member_when_one_open_pull_request_of_its_own_is_marked_ready_and_not_left():
    hub = Hub()
    hub.add(HIGH, 1, ready=False)
    hub.add(HIGH, 2, draft=True)
    hub.add(HIGH, 3, head_repo=STRANGER)  # a fork
    hub.add(HIGH, 4, left=True)
    readings = hub_readings(hub)
    assert readings.has_ready(HIGH) is False
    hub.add(HIGH, 5)
    assert readings.has_ready(HIGH) is True
    assert readings.has_ready(LOW) is False  # no pull request at all


def test_a_failed_read_of_a_repo_is_no_reading():
    hub = Hub()
    hub.fails.add(HIGH)
    assert hub_readings(hub).has_ready(HIGH) is None


def test_the_load_is_the_places_busy_count_of_its_limit_and_none_when_the_place_is_off():
    hub = Hub()
    assert hub_readings(hub, Availability(True, 18, 20, "18 busy of 20")).load() == (18, 20)
    assert hub_readings(hub, Availability(False, 0, 20, "Actions is off")).load() is None
    assert hub_readings(hub, Availability(True, 3, 0, "3 busy of 0")).load() is None


def test_a_hold_exists_only_for_a_repo_a_higher_one_of_its_account_outranks():
    hub = Hub()
    assert priority.hold_of(settings_of(), LOW, hub_readings(hub)) is None
    assert priority.hold_of(settings_of(**{LOW: 1, HIGH: 5, STRANGER: 9}), HIGH, hub_readings(hub)) is None
    assert priority.hold_of(settings_of(**{LOW: 1, SAME: 1}), LOW, hub_readings(hub)) is None
    hub.add(HIGH, 1)
    held = priority.hold_of(settings_of(**{LOW: 1, HIGH: 5}), LOW, hub_readings(hub))
    assert held is not None and held() == HIGH


# the line asks it before the place rule


def line(world, tmp_path, suite, priority_hold, **settings):
    hub, store, _, ci = world
    return run_line(hub, store, REPO, "main", settings=MergeSettings(**settings), clock=lambda: hub.now,
                    sleep=ci.sleep, root=tmp_path / "locks", holder="test line", suite=suite,
                    priority_hold=priority_hold)


def test_the_line_does_not_run_its_suite_while_held_and_runs_it_once_the_hold_ends(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    asked = []

    def hold():
        asked.append(hub.now)
        return HIGH if len(asked) < 4 else None  # the higher repo's member is served by its own line in the meantime

    done = line(world, tmp_path, suite, hold)
    assert len(asked) == 4 and passes(store) == 1
    assert {s.result for s in done.served} == {"merged"} and done.reason == "empty"
    assert asked[0] < asked[-1]  # the line waited between two readings


def test_the_line_with_no_hold_starts_as_before(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite, lambda: None)
    assert passes(store) == 1 and {s.result for s in done.served} == {"merged"}


def test_a_hold_that_lasts_the_whole_run_starts_nothing_and_every_member_stays_for_the_next_run(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite, lambda: HIGH, line_minutes=3, lock_stale_minutes=180)
    assert passes(store) == 0 and ci.merged == []
    assert {s.result for s in done.served} == {"bound"} and done.reason == "next_run"
    assert any(HIGH in note for note in done.notes)
    assert all(hub.pull(REPO, pr).state == "open" for pr in prs.values())
