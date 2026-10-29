"""The named query `test-places` (#394 decision 9; acceptance row 9): per place and OS, the runs,
their outcomes, the median run minutes and the median GitHub queue minutes. Every expected
number is worked by hand from the fixture store below:

    own/linux      3 ended runs (passed 2.0 and 4.0 minutes, failed 6.0), a fourth only started
    own/windows    1 run, no_result `cut` after 15.0 minutes (a cut run measures nothing)
    machine:a/linux 1 run, stopped
    machine:b/linux 1 run, passed after 3.0 minutes
    github/linux   4 runs: passed 1.0 and 3.0, no_result 9.0 (not measured), stopped;
                   queue waits of 1, 3 and 5 minutes on three of them, and a `test_place` wait
                   of 100 minutes that is no queue
    github/windows 2 runs: passed 10.0, failed 13.0; one queue wait of 2 minutes
"""

from datetime import UTC, datetime, timedelta

import pytest

from orch_it.cli import main
from orch_it.events import queries
from orch_it.events.store import Store

REPO = "acme-x/widgets-y"
OTHER = "acme-x/other"
COLUMNS = ["place", "os", "runs", "outcomes", "median_run_minutes", "median_queue_minutes"]


def at(clock):
    return f"2026-10-26T{clock}Z"


def envelope(**over):
    base = {"ticket": None, "role": "github", "session": None, "parent_session": None,
            "turn": None, "note": None, "repo": REPO}
    return base | over


def run_started(run, place, os_name, clock="10:00:00", **over):
    return envelope(kind="step.started", step="test_run", run=run, at=at(clock),
                    detail={"place": place, "run_os": os_name}, **over)


def run_ended(run, outcome, run_minutes=None, clock="10:30:00", reason=None, **over):
    detail = {"outcome": outcome, "reason": reason}
    if run_minutes is not None:
        detail["run_minutes"] = run_minutes
    return envelope(kind="step.ended", step="test_run", run=run, at=at(clock), detail=detail, **over)


def full_run(run, place, os_name, outcome, run_minutes=None, reason=None, **over):
    return [run_started(run, place, os_name, **over),
            run_ended(run, outcome, run_minutes, reason=reason, **over)]


def wait(run, on, seconds, reason="github_queue", **over):
    end = datetime(2026, 10, 26, 11, 0, tzinfo=UTC) + timedelta(seconds=seconds)
    return [
        envelope(kind="step.started", step="wait", run=run, at=at("11:00:00"),
                 detail={"reason": reason, "on": on}, **over),
        envelope(kind="step.ended", step="wait", run=run, at=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 detail={"outcome": "passed", "reason": None}, **over)]


def fixture_events():
    events = []
    events += full_run("own:1", "own", "linux", "passed", 2.0)
    events += full_run("own:2", "own", "linux", "passed", 4.0)
    events += full_run("own:3", "own", "linux", "failed", 6.0, "red")
    events.append(run_started("own:4", "own", "linux"))
    events += full_run("own:5", "own", "windows", "no_result", 15.0, "cut")
    events += full_run("m:1", "machine:b", "linux", "passed", 3.0)
    events += full_run("m:2", "machine:a", "linux", "stopped")
    events += full_run("g:1", "github", "linux", "passed", 1.0)
    events += full_run("g:2", "github", "linux", "passed", 3.0)
    events += full_run("g:3", "github", "linux", "no_result", 9.0, "summary_missing")
    events += full_run("g:4", "github", "linux", "stopped", reason="superseded")
    events += full_run("g:5", "github", "windows", "passed", 10.0)
    events += full_run("g:6", "github", "windows", "failed", 13.0, "red")
    events += wait("w:1", "g:1", 60)
    events += wait("w:2", "g:2", 180)
    events += wait("w:3", "g:3", 300)
    events += wait("w:4", "g:1", 6000, reason="test_place")
    events += wait("w:5", "g:5", 120)
    return events


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


def store_with(tmp_path, events):
    store = Store.open(tmp_path / "store")
    with store.writer() as writer:
        for event in events:
            writer.write(event)
    return store


@pytest.fixture
def places(tmp_path):
    return queries.test_places(store_with(tmp_path, fixture_events()), REPO)


def row_of(result, place, os_name):
    found = [row for row in result.rows if row[:2] == (place, os_name)]
    assert len(found) == 1
    return dict(zip(result.columns, found[0]))


def test_the_columns_are_place_os_runs_outcomes_and_the_two_medians(places):
    assert places.columns == COLUMNS


def test_rows_run_own_then_machines_by_id_then_github_each_by_os(places):
    assert [row[:2] for row in places.rows] == [
        ("own", "linux"), ("own", "windows"), ("machine:a", "linux"), ("machine:b", "linux"),
        ("github", "linux"), ("github", "windows")]


def test_runs_counts_the_ended_test_runs_only(places):
    assert row_of(places, "own", "linux")["runs"] == 3  # a fourth only started
    assert row_of(places, "github", "linux")["runs"] == 4


def test_outcomes_list_each_outcome_that_happened_in_the_closed_sets_order(places):
    assert row_of(places, "own", "linux")["outcomes"] == "passed=2 failed=1"
    assert row_of(places, "github", "linux")["outcomes"] == "passed=2 no_result=1 stopped=1"
    assert row_of(places, "machine:a", "linux")["outcomes"] == "stopped=1"


def test_the_median_run_minutes_is_over_passed_and_failed_runs(places):
    assert row_of(places, "own", "linux")["median_run_minutes"] == "4.0"
    assert row_of(places, "machine:b", "linux")["median_run_minutes"] == "3.0"


def test_a_run_that_is_no_result_measures_nothing_even_with_run_minutes(places):
    assert row_of(places, "github", "linux")["median_run_minutes"] == "2.0"  # 9.0 is left out
    assert row_of(places, "own", "windows")["median_run_minutes"] == "n/a"


def test_the_median_of_two_runs_is_their_mean_to_one_decimal(places):
    assert row_of(places, "github", "windows")["median_run_minutes"] == "11.5"


def test_the_median_run_minutes_is_n_a_when_nothing_was_measured(places):
    assert row_of(places, "machine:a", "linux")["median_run_minutes"] == "n/a"


def test_the_median_queue_minutes_is_over_the_github_queue_waits_on_runs_of_that_place_and_os(places):
    assert row_of(places, "github", "linux")["median_queue_minutes"] == "3.0"  # 1, 3, 5; not the 100
    assert row_of(places, "github", "windows")["median_queue_minutes"] == "2.0"


def test_the_median_queue_minutes_is_n_a_for_other_places(places):
    for place in ("own", "machine:a", "machine:b"):
        assert row_of(places, place, "linux")["median_queue_minutes"] == "n/a"


def test_a_github_place_with_no_queue_wait_shows_n_a(tmp_path):
    result = queries.test_places(store_with(tmp_path, full_run("g:1", "github", "linux", "passed", 1.0)), REPO)
    assert row_of(result, "github", "linux")["median_queue_minutes"] == "n/a"


def test_a_queue_wait_is_counted_by_the_os_of_the_run_it_waited_on(tmp_path):
    events = (full_run("g:1", "github", "linux", "passed", 1.0)
              + full_run("g:2", "github", "windows", "passed", 1.0) + wait("w:1", "g:2", 240))
    result = queries.test_places(store_with(tmp_path, events), REPO)
    assert row_of(result, "github", "linux")["median_queue_minutes"] == "n/a"
    assert row_of(result, "github", "windows")["median_queue_minutes"] == "4.0"


def test_an_ended_run_whose_start_is_not_in_the_store_has_no_place_and_is_left_out(tmp_path):
    result = queries.test_places(store_with(tmp_path, [run_ended("x:1", "passed", 1.0)]), REPO)
    assert result.rows == []


def test_the_repo_keeps_only_its_runs(tmp_path):
    events = full_run("own:1", "own", "linux", "passed", 2.0) + full_run(
        "own:2", "own", "linux", "failed", 8.0, "red", repo=OTHER)
    store = store_with(tmp_path, events)
    assert row_of(queries.test_places(store, REPO), "own", "linux")["runs"] == 1
    assert row_of(queries.test_places(store, OTHER), "own", "linux")["median_run_minutes"] == "8.0"


def test_with_no_repo_every_repos_runs_count(tmp_path):
    events = full_run("own:1", "own", "linux", "passed", 2.0) + full_run(
        "own:2", "own", "linux", "passed", 8.0, repo=OTHER)
    result = queries.test_places(store_with(tmp_path, events))
    assert row_of(result, "own", "linux")["runs"] == 2
    assert row_of(result, "own", "linux")["median_run_minutes"] == "5.0"


def test_an_empty_store_has_the_columns_and_no_rows(tmp_path):
    result = queries.test_places(Store.open(tmp_path / "store"))
    assert result.columns == COLUMNS and result.rows == []


def test_the_command_prints_the_table(tmp_path, capsys):
    store_with(tmp_path, fixture_events())
    assert main(["--store", str(tmp_path / "store"), "events", "test-places", "--repo", REPO]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == COLUMNS
    assert lines[1].split() == ["own", "linux", "3", "passed=2", "failed=1", "4.0", "n/a"]
    assert lines[-1].split() == ["github", "windows", "2", "passed=1", "failed=1", "11.5", "2.0"]


def test_the_command_without_a_repo_reads_every_repo(tmp_path, capsys):
    store_with(tmp_path, full_run("own:1", "own", "linux", "passed", 2.0, repo=OTHER))
    assert main(["--store", str(tmp_path / "store"), "events", "test-places"]) == 0
    assert "own" in capsys.readouterr().out.splitlines()[1]
