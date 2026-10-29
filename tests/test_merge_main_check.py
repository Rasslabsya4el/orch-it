"""Main is checked after every merge (#400 decision 4; row 5 on fixture GitHub): a red run of the
`tests` workflow on main opens one red-on-main ticket in #398's shape for the tests no open one
lists, a second red run on the same failures opens none, a green run closes it; a run with no
result runs once more and a second goes to the brain."""

import json
from datetime import UTC, datetime

import pytest
from fake_github import FAILED, NO_RESULT, PASSED, PENDING, REPO, FakeGitHub

from orch_it import tickets
from orch_it.events.store import Store
from orch_it.merge import red_main
from orch_it.merge.main_check import check_main
from orch_it.merge.settings import MergeSettings
from required_names import LINUX_CHECK, WINDOWS_CHECK

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RED = {LINUX_CHECK: FAILED, WINDOWS_CHECK: PASSED}
GREEN = {LINUX_CHECK: PASSED, WINDOWS_CHECK: PASSED}


@pytest.fixture
def world(tmp_path):
    return FakeGitHub(NOW), Store.open(tmp_path / "events")


def check(hub, store):
    return check_main(hub, store, REPO, "main")


def red_tickets(hub):
    return {n: red_main.ids_of(hub.issues[(REPO, n)].body) for n in hub.issues_with_label(REPO, "priority")}


def test_a_red_main_run_opens_one_red_on_main_ticket_in_the_ticket_shape(world):
    hub, store = world
    sha = hub.land(number=57)
    hub.main_run(sha, RED, failing=["tests/test_a.py::test_x", "tests/test_b.py::test_y"])
    done = check(hub, store)
    assert done.outcome == "red" and len(done.opened) == 1
    [number] = done.opened
    issue = hub.issues[(REPO, number)]
    assert tickets.violations(tickets.parse(issue.body, title=issue.title, labels=issue.labels)) == []
    assert set(issue.labels) == {"ready-for-agent", "priority"} and "#57" in issue.body
    assert red_tickets(hub) == {number: {"tests/test_a.py::test_x", "tests/test_b.py::test_y"}}
    [row] = store.query("SELECT detail FROM events WHERE kind = 'main.red'").rows
    assert json.loads(row[0]) == {"sha": sha, "test_ids": ["tests/test_a.py::test_x", "tests/test_b.py::test_y"],
                                  "introduced_by_pr": 57}


def test_a_second_red_run_on_the_same_failures_opens_none_and_a_green_run_closes_it(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::a"])
    [number] = check(hub, store).opened
    hub.later(minutes=10)
    hub.main_run(hub.land(), RED, failing=["t::a"], run_id="again")
    again = check(hub, store)
    assert again.opened == [] and again.outcome == "red"
    hub.later(minutes=10)
    hub.main_run(hub.land(), GREEN, run_id="fixed", passed=["t::a"])
    done = check(hub, store)
    assert (done.outcome, done.closed) == ("green", [number])
    assert not hub.issues[(REPO, number)].open and hub.issues[(REPO, number)].state_reason == "completed"
    assert check(hub, store).closed == []  # a second look at the same green run changes nothing


def test_new_failures_beside_listed_ones_get_their_own_ticket_and_a_fixed_ticket_closes(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::a"])
    [first] = check(hub, store).opened
    hub.later(minutes=10)
    hub.main_run(hub.land(), RED, failing=["t::a", "t::b"], run_id="two")
    [second] = check(hub, store).opened
    assert red_tickets(hub) == {first: {"t::a"}, second: {"t::b"}}
    hub.later(minutes=10)
    hub.main_run(hub.land(), RED, failing=["t::b"], passed=["t::a"], run_id="a fixed")
    done = check(hub, store)
    assert done.closed == [first] and done.opened == [] and hub.issues[(REPO, second)].open


def test_no_result_runs_once_more_and_a_second_goes_to_the_brain(world):
    hub, store = world
    sha = hub.land()
    run_id = hub.main_run(sha, {LINUX_CHECK: NO_RESULT, WINDOWS_CHECK: PASSED})
    assert check(hub, store).outcome == "rerun" and ("rerun", run_id) in hub.calls
    hub.main_runs.pop(0)
    hub.main_run(sha, {LINUX_CHECK: NO_RESULT, WINDOWS_CHECK: PASSED}, attempt=2, run_id=run_id)
    done = check(hub, store)
    assert done.outcome == "no_result" and len(done.brain) == 1
    issue = hub.issues[(REPO, done.brain[0])]
    assert "needs-triage" in issue.labels and "ready-for-agent" not in issue.labels
    assert tickets.violations(tickets.parse(issue.body, title=issue.title, labels=issue.labels)) == []
    assert check(hub, store).brain == []  # one triage ticket per run, never two


def test_a_red_run_that_names_no_test_is_no_result(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=None)
    assert check(hub, store).outcome == "rerun"


def test_a_run_still_going_is_left_for_the_next_check_and_the_ended_one_before_it_counts(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::a"])
    hub.later(minutes=5)
    hub.main_run(hub.land(), {LINUX_CHECK: PENDING, WINDOWS_CHECK: PASSED}, run_id="going")
    done = check(hub, store)
    assert done.outcome == "red" and len(done.opened) == 1


def test_no_run_at_all_is_nothing_to_do(world):
    hub, store = world
    assert check(hub, store).outcome == "no_run"


@pytest.mark.parametrize("ids", [
    [f"tests/test_{n}.py::test_{'x' * 80}" for n in range(3000)],  # more than one body lists
    ["t::" + "y" * 70_000],  # one id longer than a body
    ["t::a ", "t::b\nwith a line end", "t::````", "t::```\n## not a heading"],  # odd characters
])
def test_the_same_failures_never_open_a_second_ticket_however_their_ids_look(world, ids):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=ids)
    assert len(check(hub, store).opened) == 1
    for again in range(2):
        hub.later(minutes=10)
        hub.main_run(hub.land(), RED, failing=ids, run_id=f"again-{again}")
        done = check(hub, store)
        assert (done.opened, done.closed) == ([], []), again


def test_a_red_ticket_whose_ids_a_person_broke_still_closes_on_green(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::a"])
    [number] = check(hub, store).opened
    issue = hub.issues[(REPO, number)]
    hub.add(number, issue.body.replace("```text", "``"), issue.labels, title=issue.title)
    hub.later(minutes=10)
    hub.main_run(hub.land(), GREEN, run_id="green")
    assert check(hub, store).closed == [number]


def test_a_red_run_with_a_job_of_no_result_closes_nothing(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::windows_only"])
    [number] = check(hub, store).opened
    hub.later(minutes=10)
    hub.main_run(hub.land(), {LINUX_CHECK: FAILED, WINDOWS_CHECK: NO_RESULT}, failing=["t::other"], run_id="half")
    done = check(hub, store)
    assert number not in done.closed and hub.issues[(REPO, number)].open


def test_a_rerun_github_refuses_is_a_rerun_already_asked_for(world):
    from orch_it.testruns.github import GithubError

    hub, store = world
    hub.main_run(hub.land(), {LINUX_CHECK: NO_RESULT, WINDOWS_CHECK: PASSED})

    def refused(repo, run_id):
        raise GithubError("gh_failed", "This workflow is already running (HTTP 403)")

    hub.rerun = refused
    assert check(hub, store).outcome == "rerun"


def test_the_ticket_to_the_brain_is_not_a_red_ticket(world):
    hub, store = world
    sha = hub.land()
    hub.main_run(sha, {LINUX_CHECK: NO_RESULT, WINDOWS_CHECK: PASSED}, attempt=2)
    done = check(hub, store)
    assert done.opened == [] and len(done.brain) == 1


def test_a_commit_with_no_pull_request_is_named_as_such(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::a"])
    [number] = check(hub, store).opened
    assert "a commit with no pull request" in hub.issues[(REPO, number)].body


def test_a_green_run_that_did_not_run_the_ids_keeps_the_ticket_open(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::slow_one"])
    [number] = check(hub, store).opened
    hub.later(minutes=10)
    hub.main_run(hub.land(), GREEN, run_id="a per-change green", passed=["t::another"])
    done = check(hub, store)
    assert (done.outcome, done.closed) == ("green", []) and hub.issues[(REPO, number)].open
    hub.later(minutes=10)
    hub.main_run(hub.land(), GREEN, run_id="a green with no report", failing=None)
    assert check(hub, store).closed == [] and hub.issues[(REPO, number)].open


def test_a_green_run_that_ran_and_passed_the_ids_closes_the_ticket(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::slow_one", "t::two"])
    [number] = check(hub, store).opened
    hub.later(minutes=10)
    hub.main_run(hub.land(), GREEN, run_id="the night", passed=["t::slow_one", "t::two", "t::more"])
    assert check(hub, store).closed == [number] and not hub.issues[(REPO, number)].open


def test_a_green_run_that_passed_only_some_of_the_ids_keeps_the_ticket_open(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::slow_one", "t::two"])
    [number] = check(hub, store).opened
    hub.later(minutes=10)
    hub.main_run(hub.land(), GREEN, run_id="half", passed=["t::slow_one"])
    assert check(hub, store).closed == [] and hub.issues[(REPO, number)].open


def test_a_red_run_closes_a_ticket_only_for_ids_it_ran_and_passed(world):
    hub, store = world
    hub.main_run(hub.land(), RED, failing=["t::a", "t::b"])
    [both] = check(hub, store).opened
    hub.later(minutes=10)
    hub.main_run(hub.land(), RED, failing=["t::c"], run_id="ran only one", passed=["t::a"])
    done = check(hub, store)
    assert both not in done.closed and hub.issues[(REPO, both)].open
    hub.later(minutes=10)
    hub.main_run(hub.land(), RED, failing=["t::c"], run_id="ran both", passed=["t::a", "t::b"])
    assert both in check(hub, store).closed


def test_a_job_that_did_not_run_the_id_keeps_the_ticket_open_though_another_job_passed_it(world):
    from orch_it.merge.hub import Result

    hub, store = world
    hub.main_run(hub.land(), {LINUX_CHECK: PASSED, WINDOWS_CHECK: FAILED}, failing=["t::windows_only"])
    [number] = check(hub, store).opened
    hub.later(minutes=10)  # a per-change green: the thin Windows job left the test out, Linux passed it
    hub.main_run(hub.land(), GREEN, run_id="per change", results=[Result(LINUX_CHECK, "t::windows_only", "passed", False),
                                                           Result(WINDOWS_CHECK, "t::marked", "passed", True)])
    assert check(hub, store).closed == [] and hub.issues[(REPO, number)].open
    hub.later(minutes=10)  # the green night: both jobs ran it
    hub.main_run(hub.land(), GREEN, run_id="night", results=[Result(LINUX_CHECK, "t::windows_only", "passed", False),
                                                           Result(WINDOWS_CHECK, "t::windows_only", "passed", False)])
    assert check(hub, store).closed == [number]


def test_no_report_is_read_when_no_red_on_main_ticket_is_open(world):
    """The artifact list and the downloads of a run are GitHub requests: a run with no ticket to close reads none."""

    hub, store = world
    reads = []
    results = hub.results
    hub.results = lambda repo, run_id: reads.append(run_id) or results(repo, run_id)
    hub.main_run(hub.land(), GREEN, passed=["t::a"])
    assert check(hub, store).outcome == "green" and reads == []
    hub.later(minutes=10)
    hub.main_run(hub.land(), RED, failing=["t::b"], run_id="red")
    [number] = check(hub, store).opened
    assert reads == []  # a red run with a ticket to open and none open before reads none
    hub.later(minutes=10)
    hub.main_run(hub.land(), GREEN, passed=["t::b"], run_id="fixed")
    assert check(hub, store).closed == [number] and reads == ["fixed"]
