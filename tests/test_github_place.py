"""The `github` place (#414 item 2, rows 2 and 14): the place rule offers GitHub with its measured finish, the
adapter runs the required suite there for a sha, the busy machines are the account's, and a run is timed from its
jobs' queueing to their end. `FakeActions` is GitHub's Actions API for one account (tests/fake_github.py)."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fake_github import DispatchedJob, FakeActions
from machine_repos import Repos
from required_names import LINUX_CHECK, WINDOWS_CHECK

from orch_it.events import github_budget
from orch_it.events.store import Store
from orch_it.testruns.config import TestSettings
from orch_it.testruns.github import GithubError
from orch_it.testruns.github_place import GithubPlace, oses_of_workflow
from orch_it.testruns.places import PlaceEnd
from orch_it.testruns.runner import offers_of, run_tests

REPO = "acme-x/widgets-y"
OTHER = "acme-x/gadgets-z"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
GREEN_JOBS = {LINUX_CHECK: DispatchedJob(), WINDOWS_CHECK: DispatchedJob(label="windows-latest")}
JUNIT = "<testsuites><testsuite name='pytest'><testcase classname='t' name='test_a'/></testsuite></testsuites>"


class StubOwn:
    """The own place without a process: a green report at once."""

    id, os = "own", "linux"

    def run(self, request, scratch):
        report = scratch / "junit.xml"
        report.write_text(JUNIT, "utf-8")
        return PlaceEnd(report, False, False, 0, 0.0, 1.0)


def seed(store, place, minutes, *, run_os=None):
    """A past passed run of `place` that took `minutes`, on the repo's full selection."""

    with store.writer() as writer:
        envelope = {"repo": REPO, "ticket": None, "role": "script", "session": None, "parent_session": None,
                    "turn": None, "step": "test_run", "run": f"seed-{place}-{run_os}", "note": None}
        writer.write(envelope | {"kind": "step.started", "at": "2026-10-29T10:00:00+00:00", "detail": {
            "place": place, "selection": "full", "run_os": run_os, "bound_minutes": 15}})
        writer.write(envelope | {"kind": "step.ended", "at": "2026-10-29T10:01:00+00:00", "detail": {
            "outcome": "passed", "reason": None, "run_minutes": minutes, "start_minutes": 0.0}})


@pytest.fixture
def world(tmp_path):
    actions = FakeActions(tmp_path, REPO, NOW - timedelta(minutes=30), jobs=dict(GREEN_JOBS), now=NOW)
    return SimpleNamespace(repos=Repos(tmp_path), store=Store.open(tmp_path / "store"), actions=actions,
                           settings=TestSettings(github_tests=True))


def github(world, **kwargs):
    kwargs.setdefault("oses", ("linux", "windows"))
    kwargs.setdefault("gh", world.actions)
    return GithubPlace(repo=REPO, store=world.store, settings=world.settings, sleep=lambda seconds: None, **kwargs)


def run(world, place=None, *, need_os="any", forced=None, settings=None):
    return run_tests(world.store, work_tree=world.repos.caller, repo=REPO, settings=settings or world.settings,
                     places={"own": StubOwn(), "github": place or github(world)}, need_os=need_os,
                     place=forced, now=lambda: NOW, sleep=lambda seconds: None)


def starts(store):
    return [json.loads(d) for (d,) in store.query(
        "SELECT detail FROM events WHERE kind = 'step.started' AND step = 'test_run' AND role = 'script'"
        " AND run NOT LIKE 'seed-%' ORDER BY at, id").rows]


def offered(start, place="github"):
    return next(o for o in start["places_offered"] if o["place"] == place)


# --- the rule offers github with its measured finish (row 2) ------------------------------------------

def test_with_github_measured_fastest_the_rule_picks_github_and_the_test_run_says_so(world):
    seed(world.store, "own", 5.0)
    seed(world.store, "github", 1.0)
    done = run(world)
    assert (done.place, done.outcome) == ("github", "passed")
    [start] = starts(world.store)
    assert (start["place"], start["chosen"], start["reason"]) == ("github", "github", "earliest_finish")
    assert offered(start) == {"place": "github", "expected_minutes": 1.0, "dropped": None, "note": "0 busy of 20"}


def test_with_own_faster_the_rule_picks_own_and_places_offered_still_lists_github(world):
    seed(world.store, "own", 1.0)
    seed(world.store, "github", 5.0)
    done = run(world)
    assert done.place == "own"
    [start] = starts(world.store)
    assert (start["place"], start["reason"]) == ("own", "earliest_finish")
    assert offered(start)["expected_minutes"] == 5.0 and offered(start)["dropped"] is None
    assert world.actions.calls and not any("dispatches" in " ".join(c) for c in world.actions.calls)


def test_github_stays_switched_off_while_the_setting_github_tests_is_off(world):
    done = run(world, settings=TestSettings())
    assert done.place == "own"
    assert offered(starts(world.store)[0]) == {"place": "github", "expected_minutes": None, "dropped": "place_off"}


def test_checks_named_unit_and_lint_still_offer_github_for_linux_and_windows_needs(world):
    workflow = ("name: tests\non: push\njobs:\n  unit:\n    runs-on: ubuntu-latest\n    steps: []\n"
                "  lint:\n    name: lint\n    runs-on: windows-latest\n    steps: []\n  docs:\n"
                "    runs-on: macos-latest\n")
    assert oses_of_workflow(workflow, ("unit", "lint")) == ("linux", "windows")
    world.settings = TestSettings(github_tests=True, required_checks=("unit", "lint"))
    place = GithubPlace.for_work_tree(world.repos.caller, repo=REPO, store=world.store, settings=world.settings,
                                      gh=world.actions, workflow_text=workflow)
    [github_offer] = [o for o in offers_of(world.settings, {"own": StubOwn(), "github": place}) if o.place == "github"]
    assert github_offer.oses == ("linux", "windows")
    world.actions.dispatch_jobs = {"unit": DispatchedJob(), "lint": DispatchedJob(label="windows-latest")}
    for need in ("linux", "windows"):
        seed(world.store, "own", 5.0, run_os=need)
        seed(world.store, "github", 1.0, run_os=need)
        done = run(world, place, need_os=need)
        assert done.place == "github", need


def test_the_oses_come_from_each_required_jobs_runner_label_and_a_matrix_label_gives_none():
    text = "jobs:\n  a:\n    runs-on: [self-hosted, linux]\n  b:\n    runs-on: ${{ matrix.os }}\n"
    assert oses_of_workflow(text, ("a", "b")) == ("linux",)
    assert oses_of_workflow(text, ("b",)) == ()


def test_without_a_workflow_file_the_oses_are_those_of_the_stored_github_runs(world):
    with world.store.writer() as writer:
        writer.write({"kind": "step.started", "repo": REPO, "ticket": None, "role": "github", "session": None,
                      "parent_session": None, "turn": None, "step": "test_run", "run": "github:1/2/1",
                      "note": None, "detail": {"place": "github", "run_os": "windows"}})
    place = GithubPlace.for_work_tree(world.repos.caller, repo=REPO, store=world.store, settings=world.settings,
                                      gh=world.actions)
    assert place.oses == ("windows",)


# --- the machines busy are the account's, not this repo's (row 14) --------------------------------------

def test_busy_machines_are_counted_across_the_accounts_repos_not_this_repos_jobs(world):
    world.actions.hold(OTHER, 18)
    assert world.actions.runs[REPO] == []  # this repo's own jobs: 0
    seed(world.store, "own", 5.0)
    seed(world.store, "github", 1.0)
    done = run(world)
    assert done.place == "github"
    [start] = starts(world.store)
    assert offered(start)["note"] == "18 busy of 20"
    assert offered(start)["dropped"] is None


def test_an_account_at_its_limit_drops_github_as_busy_and_says_how_many(world):
    world.actions.hold(OTHER, 20)
    seed(world.store, "own", 5.0)
    seed(world.store, "github", 1.0)
    done = run(world)
    assert done.place == "own"
    assert offered(starts(world.store)[0]) == {"place": "github", "expected_minutes": None, "dropped": "busy",
                                               "note": "20 busy of 20"}


def test_the_setting_github_capacity_is_the_limit_while_no_job_has_waited(world):
    world.settings = TestSettings(github_tests=True, github_capacity=4)
    world.actions.hold(OTHER, 3)
    run(world)
    assert offered(starts(world.store)[0])["note"] == "3 busy of 4"


def test_a_job_that_waited_in_the_queue_while_others_ran_gives_the_limit_the_runs_show(world):
    world.actions.hold(OTHER, 8, queued=2, queued_for=300)  # 2 jobs wait 5 minutes while 8 run: the limit is 8
    run(world)
    start = starts(world.store)[0]
    assert offered(start)["note"] == "8 busy of 8" and offered(start)["dropped"] == "busy"


def test_a_job_that_only_just_queued_does_not_make_the_runs_show_a_limit(world):
    world.actions.hold(OTHER, 8, queued=2, queued_for=2)
    run(world)
    assert offered(starts(world.store)[0])["note"] == "8 busy of 20"


# --- off, out of requests ------------------------------------------------------------------------------

def test_github_is_not_offered_while_actions_is_off_and_the_decision_says_so(world):
    seed(world.store, "own", 5.0)
    seed(world.store, "github", 1.0)
    world.actions.enabled = False
    done = run(world)
    assert done.place == "own"
    entry = offered(starts(world.store)[0])
    assert entry["dropped"] == "place_off" and "Actions is off" in entry["note"]


def test_github_is_not_offered_when_the_actions_budget_would_cross_its_threshold(world):
    seed(world.store, "own", 5.0)
    seed(world.store, "github", 1.0)
    github_budget.record_budget(world.store, REPO, budget="actions", remaining=150, limit=1000, at=NOW)
    done = run(world)
    assert done.place == "own"
    entry = offered(starts(world.store)[0])
    assert entry["dropped"] == "place_off" and "budget would cross" in entry["note"]
    assert not any("dispatches" in " ".join(c) for c in world.actions.calls)


def test_github_is_offered_when_the_actions_budget_has_room(world):
    seed(world.store, "own", 5.0)
    seed(world.store, "github", 1.0)
    github_budget.record_budget(world.store, REPO, budget="actions", remaining=990, limit=1000, at=NOW)
    assert run(world).place == "github"


def test_a_gh_that_fails_on_the_account_lists_leaves_github_off_with_the_code(world):
    def broken(args):
        raise GithubError("gh_failed", "no network")

    done = run(world, github(world, gh=broken))
    assert done.place == "own"
    entry = offered(starts(world.store)[0])
    assert entry["dropped"] == "place_off" and "gh_failed" in entry["note"]


# --- the run: dispatch for a sha, read through the importer, timed by the jobs (row 14) -----------------

def test_a_run_dispatches_tests_yml_on_a_ref_made_for_the_sha_and_removes_the_ref(world):
    done = run(world, forced="github")
    assert done.outcome == "passed"
    sha = world.repos.old
    [(ref, at)] = world.actions.refs.items()
    assert at == sha
    assert world.actions.deleted == [ref]
    assert world.actions.runs[REPO][0]["head_sha"] == sha and world.actions.runs[REPO][0]["event"] == "workflow_dispatch"


def test_the_stored_time_is_the_jobs_queued_to_finished_not_the_runs_own_status(world):
    # linux is queued at +0 and finishes at +400, windows is queued at +20 and finishes at +380; the run's own
    # record stays in progress and was last updated at +1100
    world.actions.dispatch_jobs = {LINUX_CHECK: DispatchedJob(queued=0, started=130, completed=400),
                                   WINDOWS_CHECK: DispatchedJob(label="windows-latest", queued=20, started=60,
                                                                completed=380)}
    done = run(world, forced="github")
    assert done.outcome == "passed"
    [(run_minutes, start_minutes)] = world.store.query(
        "SELECT run_minutes, start_minutes FROM events WHERE kind = 'step.ended' AND step = 'test_run'"
        " AND role = 'script'").rows
    assert abs(run_minutes * 60 - 400) <= 5
    assert start_minutes == 0


def test_the_importer_stores_the_runs_jobs_under_the_github_role(world):
    run(world, forced="github")
    rows = world.store.query("SELECT detail FROM events WHERE kind = 'step.started' AND step = 'test_run'"
                             " AND role = 'github'").rows
    assert sorted(json.loads(d)["run_os"] for (d,) in rows) == ["linux", "windows"]


def test_a_red_case_of_a_job_is_a_failed_run_and_a_job_with_no_report_is_no_result(world):
    world.actions.dispatch_jobs = {LINUX_CHECK: DispatchedJob(conclusion="failure", cases=[("t", "a", "failed")])}
    assert run(world, forced="github").outcome == "failed"
    world.actions.dispatch_jobs = {LINUX_CHECK: DispatchedJob(conclusion="failure", cases=None)}
    done = run(world, forced="github")
    assert (done.outcome, done.reason) == ("no_result", "summary_missing")


def test_a_job_still_running_at_the_bound_is_a_cut_run_and_the_run_is_cancelled(world):
    world.actions.dispatch_jobs = {LINUX_CHECK: DispatchedJob(completed=None)}
    world.settings = TestSettings(github_tests=True, bound_minutes=1)
    done = run(world, forced="github")
    assert (done.outcome, done.reason) == ("no_result", "cut")
    assert len(world.actions.cancelled) == 1


def test_a_sha_github_does_not_have_is_a_refusal_and_the_run_goes_to_the_next_place(world):
    world.actions.known_shas = set()
    seed(world.store, "own", 5.0)
    seed(world.store, "github", 1.0)
    done = run(world)
    assert done.place == "own"
    ends = world.store.query("SELECT outcome, reason FROM events WHERE kind = 'step.ended' AND step = 'test_run'"
                             " AND role = 'script' AND run NOT LIKE 'seed-%' ORDER BY at, id").rows
    assert ends == [("refused", "place_off"), ("passed", None)]


def test_a_second_dispatch_for_the_same_sha_reads_only_its_own_run(world):
    run(world, forced="github")
    world.actions.at = world.actions.at + timedelta(minutes=10)
    done = run(world, forced="github")
    assert done.outcome == "passed"
    assert len(world.actions.runs[REPO]) == 2
