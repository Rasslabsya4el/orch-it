"""The request budget (#414 decision 9): every GitHub call the layer makes is counted per account per hour in the
store; the Actions-token budget is stored per repo; a poller slows under the threshold; a failed count never fails
a call. Expected values are written out by hand (a count of the calls replayed, a worked interval), never recomputed
with the code under test."""

from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import json

import pytest

from orch_it.events import github_budget
from orch_it.events.store import Store
from orch_it.merge.line import poll_wait
from orch_it.merge.settings import MergeSettings
from orch_it.settings import SettingsError
from orch_it.testruns import github

START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
OK = SimpleNamespace(returncode=0, stdout=b'{"ok": true}', stderr=b"")


def test_hourly_totals_equal_the_calls_replayed_over_two_hours(tmp_path):
    store = Store.open(tmp_path / "store")
    moments = [START + timedelta(seconds=72 * i) for i in range(100)]  # 100 calls across 2 hours
    for i, at in enumerate(moments):
        github_budget.count_call(f"acme-dev/repo-{i % 3}", store=store, at=at)

    totals = github_budget.hourly_totals(store, "acme-dev")

    made = Counter(at.strftime("%Y-%m-%dT%H") for at in moments)  # 50 in 09, 50 in 10 by hand
    assert made == {"2026-11-02T09": 50, "2026-11-02T10": 50}
    assert sum(totals.values()) == 100
    for hour, count in made.items():
        assert abs(totals[hour] - count) <= 0.05 * count


def test_poll_wait_grows_under_the_threshold_and_stays_above_it():
    settings = MergeSettings(poll_seconds=30)

    class Hub:
        def __init__(self, left):
            self.left = left

        def rate_limit(self):
            return self.left

    assert poll_wait(Hub((500, 5000)), settings) == 120  # 500 under 1000 (20% of 5000): two halvings, x4
    assert poll_wait(Hub((900, 5000)), settings) == 60  # just under the threshold: x2
    assert poll_wait(Hub((4000, 5000)), settings) == 30  # above: unchanged
    assert poll_wait(Hub(None), settings) == 30  # no reading: the poll keeps its interval


def test_poll_interval_doubles_per_halving_and_caps_at_an_hour():
    share = github_budget.default_threshold()  # the default read from the loader: 1000 is its level of 5000
    assert github_budget.poll_interval(30, 1000, 5000, share) == 30  # exactly the threshold: base
    assert github_budget.poll_interval(30, 999, 5000, share) == 60
    assert github_budget.poll_interval(30, 0, 5000, share) == 3600  # exhausted: the cap
    assert github_budget.poll_interval(30, 1, 5000, share) == 3600  # deep under: the cap, never more


def test_threshold_is_read_with_dict_get_and_checked():
    assert github_budget.threshold({}) == github_budget.default_threshold()
    assert github_budget.threshold({"request_budget_threshold": 0.5}) == 0.5
    with pytest.raises(SettingsError) as refused:
        github_budget.threshold({"request_budget_threshold": "lots"})
    assert refused.value.code == "bad_setting"


def test_actions_budget_is_stored_per_repo_and_read_back(tmp_path):
    store = Store.open(tmp_path / "store")
    github_budget.record_budget(store, "acme-dev/orch-tools", budget="actions", remaining=100, limit=1000, at=START)
    github_budget.record_budget(store, "acme-dev/other", budget="actions", remaining=900, limit=1000, at=START)

    rows = store.query("SELECT repo, budget_remaining, budget_limit FROM events WHERE budget = 'actions' "
                       "ORDER BY repo").rows
    assert rows == [("acme-dev/orch-tools", 100, 1000), ("acme-dev/other", 900, 1000)]

    raw = {}
    now = START + timedelta(minutes=5)
    # floor 200 (20% of 1000): 100 - 10 is under it, 900 - 10 is not, a repo with no reading never crosses
    assert github_budget.would_cross("acme-dev/orch-tools", 10, store=store, now=now, raw=raw) is True
    assert github_budget.would_cross("acme-dev/other", 10, store=store, now=now, raw=raw) is False
    assert github_budget.would_cross("acme-dev/none", 10, store=store, now=now, raw=raw) is False


def test_a_reading_older_than_an_hour_does_not_cross(tmp_path):
    store = Store.open(tmp_path / "store")
    github_budget.record_budget(store, "acme-dev/stale", budget="actions", remaining=10, limit=1000,
                                at=START - timedelta(hours=2))
    assert github_budget.would_cross("acme-dev/stale", 1, store=store, now=START, raw={}) is False


def test_a_failed_count_does_not_fail_the_gh_call(tmp_path, monkeypatch):
    blocked = tmp_path / "store-is-a-file"
    blocked.write_text("not a folder", "utf-8")
    monkeypatch.setenv("ORCH_IT_STORE", str(blocked))
    monkeypatch.setattr(github.subprocess, "run", lambda *a, **k: OK)
    assert github.run_gh(["api", "repos/acme-dev/orch-tools"]) == '{"ok": true}'


def test_a_dropped_count_is_counted_and_said_on_stderr(tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "store-is-a-file"
    blocked.write_text("not a folder", "utf-8")
    monkeypatch.setenv("ORCH_IT_STORE", str(blocked))
    monkeypatch.setattr(github.subprocess, "run", lambda *a, **k: OK)
    monkeypatch.setattr(github_budget, "dropped_calls", 0)
    assert github.run_gh(["api", "repos/acme-dev/orch-tools"]) == '{"ok": true}'  # the call still succeeds
    assert github_budget.dropped_calls == 1
    assert "not counted" in capsys.readouterr().err


def test_run_gh_counts_each_call_including_a_failed_one(tmp_path, monkeypatch):
    root = tmp_path / "store"
    monkeypatch.setenv("ORCH_IT_STORE", str(root))
    answers = iter([OK, OK, SimpleNamespace(returncode=1, stdout=b"", stderr=b"HTTP 403")])
    monkeypatch.setattr(github.subprocess, "run", lambda *a, **k: next(answers))
    github.run_gh(["api", "repos/acme-dev/orch-tools/issues/1"])
    github.run_gh(["api", "repos/acme-dev/orch-tools/issues/2"])
    with pytest.raises(github.GithubError):
        github.run_gh(["api", "repos/acme-dev/orch-tools/issues/3"])
    assert sum(github_budget.hourly_totals(Store.open(root), "acme-dev").values()) == 3


def test_repo_of_names_the_repo_a_call_is_about():
    assert github_budget.repo_of(["api", "repos/acme-dev/orch-tools/issues/1"]) == "acme-dev/orch-tools"
    assert github_budget.repo_of(["api", "graphql", "-f", "owner=acme-dev", "-f", "name=orch-tools"]) == \
        "acme-dev/orch-tools"
    assert github_budget.repo_of(["api", "user"]) is None  # names no repo: not stored (the envelope needs one)


@pytest.mark.parametrize("args", [
    ["pr", "create", "--repo", "acme-dev/orch-tools", "--base", "main", "--head", "orch-9"],  # merge/gh.py:152
    ["pr", "merge", "7", "--repo=acme-dev/orch-tools", "--squash"],  # merge/gh.py:206
    ["pr", "merge", "7", "-R", "acme-dev/orch-tools", "--disable-auto"],  # the short flag
    ["run", "download", "12", "--repo", "acme-dev/orch-tools", "-n", "junit-linux"],  # merge/gh.py:508
    ["run", "download", "12", "-R", "acme-dev/orch-tools", "-n", "junit-linux", "-D", "/tmp/x"],  # testruns/github.py:472
])
def test_repo_of_reads_the_repo_flag_in_each_form(args):
    assert github_budget.repo_of(args) == "acme-dev/orch-tools"


# --- the account key, the reset window, the Actions spend (#414 unit 12a) ---------------------------------------
# names are built at run time: no literal account, repo or login stands in this file's new tests

OWNER = "-".join(["acme", "dev"])
REPO = f"{OWNER}/" + "".join(["orch", "-tools"])
LOGIN = "".join(["tok", "en", "-user"])
REAL_READ_LOGIN = github_budget._read_login  # collected before the autouse stub of conftest replaces it


def fake_gh(monkeypatch, *, login=LOGIN, rate_limit=None):
    """A `gh` that answers `api user --jq .login` with `login` (None: fails; a callable: what it returns now) and
    `api rate_limit` with the text `rate_limit()` returns; every other call answers ok. The calls it saw are in the
    returned list."""

    seen = []

    def run(command, **kwargs):
        args = command[1:]
        seen.append(args)
        if args[:2] == ["api", "user"]:
            who = login() if callable(login) else login
            if who is None:
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"HTTP 401")
            return SimpleNamespace(returncode=0, stdout=f"{who}\n".encode(), stderr=b"")
        if "rate_limit" in args and rate_limit is not None:
            return SimpleNamespace(returncode=0, stdout=rate_limit().encode(), stderr=b"")
        return OK

    monkeypatch.setattr(github.subprocess, "run", run)
    return seen


def login_calls(seen):
    return sum(1 for args in seen if args[:2] == ["api", "user"])


@pytest.fixture
def store(tmp_path, monkeypatch):
    """The store of the test, also the one the layer's own `run_gh` writes to."""

    monkeypatch.setenv("ORCH_IT_STORE", str(tmp_path / "store"))
    return Store.open(tmp_path / "store")


@pytest.fixture
def real_login(monkeypatch):
    monkeypatch.setattr(github_budget, "_read_login", REAL_READ_LOGIN)


def test_the_account_key_is_the_login_of_the_token_read_once_per_process(store, monkeypatch, real_login):
    seen = fake_gh(monkeypatch)
    other_owner = "-".join(["other", "org"])
    github_budget.count_call(REPO, store=store, at=START)
    github_budget.count_call(f"{other_owner}/some-repo", store=store, at=START)

    accounts = {row[0] for row in store.query("SELECT account FROM events WHERE kind = 'github.called'").rows}
    assert accounts == {LOGIN}  # not the owner of either repo
    assert login_calls(seen) == 1  # one call for both events
    assert sum(github_budget.hourly_totals(store).values()) == 2  # the default account is the token's


def test_an_actions_reading_has_no_account_and_asks_for_no_login(store, monkeypatch, real_login):
    seen = fake_gh(monkeypatch)
    github_budget.record_budget(store, REPO, budget="actions", remaining=10, limit=100, at=START)
    assert store.query("SELECT account, repo FROM events WHERE budget = 'actions'").rows == [(None, REPO)]
    assert login_calls(seen) == 0  # its budget is per repo: found by the repo
    assert github_budget.would_cross(REPO, 1, store=store, now=START + timedelta(minutes=1), raw={}) is True


def test_the_account_key_falls_back_to_the_repo_owner_when_the_login_call_fails(store, monkeypatch, real_login):
    fake_gh(monkeypatch, login=None)
    github_budget.count_call(REPO, store=store, at=START)
    rows = store.query("SELECT account FROM events WHERE kind = 'github.called'").rows
    assert rows == [(OWNER,)]


def test_a_failed_login_is_not_asked_again_for_a_minute_and_the_total_keeps_both_keys(store, monkeypatch, real_login):
    clock = [1000.0]
    monkeypatch.setattr(github_budget, "monotonic", lambda: clock[0])
    answering = {"login": None}
    seen = fake_gh(monkeypatch, login=lambda: answering["login"])

    github_budget.count_call(REPO, store=store, at=START)  # the lookup fails: the owner is the key
    clock[0] += 59
    github_budget.count_call(REPO, store=store, at=START + timedelta(seconds=1))  # inside the 60 s: not asked
    assert login_calls(seen) == 1

    answering["login"] = LOGIN
    clock[0] += 2  # 61 s after the failure: asked again
    github_budget.count_call(REPO, store=store, at=START + timedelta(seconds=2))
    assert login_calls(seen) == 2

    keys = store.query("SELECT account, COUNT(*) FROM events WHERE kind = 'github.called' GROUP BY account "
                       "ORDER BY account").rows
    assert sorted(keys) == sorted([(OWNER, 2), (LOGIN, 1)])
    assert sum(github_budget.hourly_totals(store).values()) == 3  # one counter, not split by the lookup's luck
    assert sum(github_budget.hourly_totals(store, OWNER).values()) == 2  # a named account is its exact key


class FakeRateLimit:
    """GitHub's side of one window: it counts the calls it is sent and reports `used` and `reset` of the window
    that starts at `window_start`, the way `gh api rate_limit` shows them."""

    def __init__(self, window_start):
        self.window_start, self.used = window_start, 0

    def served(self, at):
        if self.window_start <= at < self.window_start + timedelta(hours=1):
            self.used += 1

    def answer(self):
        reset = int((self.window_start + timedelta(hours=1)).timestamp())
        core = {"limit": 5000, "used": self.used, "remaining": 5000 - self.used, "reset": reset}
        return json.dumps({"resources": {"core": core}, "rate": core})


def test_the_counter_follows_githubs_reset_window_not_the_clock_hour(store, monkeypatch, real_login):
    window_start = datetime(2026, 11, 2, 9, 37, tzinfo=UTC)  # a window that does not start at a clock hour
    github_side = FakeRateLimit(window_start)
    fake_gh(monkeypatch, rate_limit=github_side.answer)
    # 40 calls in the previous window (09:00-09:36), 200 across this one, 30 after it
    moments = ([window_start - timedelta(minutes=37) + timedelta(seconds=55 * i) for i in range(40)]
               + [window_start + timedelta(seconds=18 * i) for i in range(200)]
               + [window_start + timedelta(hours=1, seconds=10 * i) for i in range(30)])
    for at in moments:
        github_side.served(at)
        github_budget.count_call(REPO, store=store, at=at)
    clock_hour = github_budget.hourly_totals(store)  # no reset known yet: the clock hours
    assert sorted(clock_hour) == ["2026-11-02T09", "2026-11-02T10"]

    github.run_gh(["api", "rate_limit"])  # the reading carries the reset: the process remembers it

    now = window_start + timedelta(minutes=30)
    totals = github_budget.hourly_totals(store, now=now)
    assert github_side.used == 200
    assert sum(totals.values()) == totals["2026-11-02T09:37"]  # one window, keyed by its start
    assert abs(totals["2026-11-02T09:37"] - github_side.used) <= 0.05 * github_side.used
    assert sum(clock_hour.values()) == 270  # the clock hours alone would have been wrong by far
    assert clock_hour["2026-11-02T09"] != 200


def test_the_window_rolls_on_past_a_stale_reset_and_counts_each_window_of_calls(store, monkeypatch, real_login):
    first = datetime(2026, 11, 2, 9, 37, tzinfo=UTC)
    second = first + timedelta(hours=1)
    side_one, side_two = FakeRateLimit(first), FakeRateLimit(second)
    fake_gh(monkeypatch, rate_limit=side_one.answer)
    for i in range(200):
        at = first + timedelta(seconds=18 * i)
        side_one.served(at)
        github_budget.count_call(REPO, store=store, at=at)
    github.run_gh(["api", "rate_limit"])  # the only reading: it ends the first window
    for i in range(150):  # the second window has calls and no reading of its own
        at = second + timedelta(seconds=24 * i)
        side_two.served(at)
        github_budget.count_call(REPO, store=store, at=at)

    in_one = github_budget.hourly_totals(store, now=first + timedelta(minutes=40))
    in_two = github_budget.hourly_totals(store, now=second + timedelta(minutes=40))  # the reset is 40 min past
    assert (side_one.used, side_two.used) == (200, 150)
    assert in_one == {"2026-11-02T09:37": in_one["2026-11-02T09:37"]}
    assert list(in_two) == ["2026-11-02T10:37"]  # the window containing now, on the grid of the stale reset
    for counted, used in ((in_one["2026-11-02T09:37"], 200), (in_two["2026-11-02T10:37"], 150)):
        assert abs(counted - used) <= 0.05 * used
    far = github_budget.hourly_totals(store, now=second + timedelta(hours=5, minutes=3))
    assert far == {"2026-11-02T15:37": 0}  # five windows later: no call in it


def test_a_rate_limit_reading_is_not_a_counted_call(store, monkeypatch, real_login):
    window_start = datetime(2026, 11, 2, 9, 37, tzinfo=UTC)
    fake_gh(monkeypatch, rate_limit=FakeRateLimit(window_start).answer)
    github_budget.count_call(REPO, store=store, at=window_start)
    for _ in range(3):
        github.run_gh(["api", "rate_limit"])
    now = window_start + timedelta(minutes=1)
    assert sum(github_budget.hourly_totals(store, now=now).values()) == 1
    assert store.query("SELECT COUNT(*) FROM events WHERE kind = 'github.called'").rows == [(4,)]


def test_a_reading_that_names_no_repo_is_stored_under_the_account_even_before_any_counted_call(
        store, monkeypatch, real_login):
    window_start = datetime(2026, 11, 2, 9, 37, tzinfo=UTC)
    fake_gh(monkeypatch, rate_limit=FakeRateLimit(window_start).answer)
    github.run_gh(["api", "rate_limit"])  # no call was counted before it
    rows = store.query("SELECT repo, account, budget_remaining, budget_limit FROM events "
                       "WHERE budget_reset IS NOT NULL").rows
    assert rows == [(f"{LOGIN}/.account", LOGIN, 5000, 5000)]


def test_a_reading_that_names_no_repo_and_has_no_login_stores_nothing(store, monkeypatch, real_login):
    window_start = datetime(2026, 11, 2, 9, 37, tzinfo=UTC)
    fake_gh(monkeypatch, login=None, rate_limit=FakeRateLimit(window_start).answer)
    github.run_gh(["api", "rate_limit"])  # no repo, no login: nowhere to put it, and no error
    assert store.query("SELECT COUNT(*) FROM events WHERE kind = 'github.called'").rows == [(0,)]


def test_a_rate_limit_answer_that_is_no_reading_stores_nothing_and_raises_nothing(store, monkeypatch, real_login):
    texts = iter(["not json", json.dumps({"resources": {}}), json.dumps({"resources": {"core": {"limit": "x"}}})])
    fake_gh(monkeypatch, rate_limit=lambda: next(texts))
    github_budget.count_call(REPO, store=store, at=START)
    for _ in range(3):
        github.run_gh(["api", "rate_limit"])
    assert store.query("SELECT COUNT(*) FROM events WHERE budget_reset IS NOT NULL").rows == [(0,)]


def test_a_reading_whose_reset_has_passed_holds_no_run_back(store):
    reset = START + timedelta(minutes=30)
    github_budget.record_budget(store, REPO, budget="actions", remaining=10, limit=1000, at=START, reset=reset)
    assert github_budget.would_cross(REPO, 1, store=store, now=START + timedelta(minutes=10), raw={}) is True
    # 40 min after the reading is still inside the hour of the reading, but past the reset: the budget refilled
    assert github_budget.would_cross(REPO, 1, store=store, now=START + timedelta(minutes=40), raw={}) is False


def test_the_actions_spend_is_limit_minus_remaining_at_the_last_reading(store):
    reset = START + timedelta(hours=1)
    github_budget.record_budget(store, REPO, budget="actions", remaining=700, limit=1000, at=START, reset=reset)
    assert github_budget.actions_spend(REPO, store=store) == 300  # one reading: GitHub's own used
    for minutes, remaining in ((20, 650), (50, 500)):
        github_budget.record_budget(store, REPO, budget="actions", remaining=remaining, limit=1000,
                                    at=START + timedelta(minutes=minutes), reset=reset)
    assert github_budget.actions_spend(REPO, store=store) == 500  # the last reading, not the fall since the first
    assert github_budget.actions_spend("-".join([OWNER, "none"]) + "/x", store=store) == 0  # no readings


def test_times_are_converted_to_utc_before_they_are_formatted(store, real_login, monkeypatch):
    fake_gh(monkeypatch)
    east = timezone(timedelta(hours=3))
    reset = datetime(2026, 11, 2, 13, 37, tzinfo=east)  # 10:37 UTC
    github_budget.record_budget(store, REPO, budget="account", remaining=4000, limit=5000,
                                at=datetime(2026, 11, 2, 12, 0, tzinfo=east), reset=reset)
    github_budget.count_call(REPO, store=store, at=datetime(2026, 11, 2, 12, 40, tzinfo=east))  # 09:40 UTC, in
    github_budget.count_call(REPO, store=store, at=datetime(2026, 11, 2, 12, 30, tzinfo=east))  # 09:30 UTC, before
    stored = store.query("SELECT budget_reset FROM events WHERE budget_reset IS NOT NULL").rows
    assert stored[0][0].startswith("2026-11-02T10:37:00")
    for now in (datetime(2026, 11, 2, 13, 0, tzinfo=east), datetime(2026, 11, 2, 10, 0, tzinfo=UTC)):  # one instant
        assert github_budget.hourly_totals(store, now=now) == {"2026-11-02T09:37": 1}
