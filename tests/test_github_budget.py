"""The request budget (#414 decision 9): every GitHub call the layer makes is counted per account per hour in the
store; the Actions-token budget is stored per repo; a poller slows under the threshold; a failed count never fails
a call. Expected values are written out by hand (a count of the calls replayed, a worked interval), never recomputed
with the code under test."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
