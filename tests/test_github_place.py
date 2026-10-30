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
from orch_it.testruns import place_rule, runner
from orch_it.testruns.config import DEFAULT_QUEUE_BOUND_MINUTES, TestSettings
from orch_it.testruns.github import GithubError
from orch_it.testruns.github_place import QUEUE_GRACE_SECONDS, GithubPlace, oses_of_workflow
from orch_it.testruns.places import PlaceEnd, PlaceRefused, Request
from orch_it.testruns.runner import default_places, offers_of, run_tests

REPO = "acme-x/widgets-y"
DEFAULTS = TestSettings()  # the settings of a repo that sets nothing: github_tests on, github_capacity 20
OTHER = "acme-x/gadgets-z"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
GREEN_JOBS = {LINUX_CHECK: DispatchedJob(), WINDOWS_CHECK: DispatchedJob(label="windows-latest")}
JUNIT = "<testsuites><testsuite name='pytest'><testcase classname='t' name='test_a'/></testsuite></testsuites>"


class Clock:
    """Time that only moves when the code sleeps or a `gh` call takes its seconds."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class SlowGh:
    """`gh` that takes `seconds` of the clock per call; the dispatched run is listed only `appear_after` seconds
    after the dispatch call; `ref_at` and `cancels_at` keep the clock at the branch made and at each cancel."""

    def __init__(self, actions, clock, seconds, appear_after=0.0):
        self.actions, self.clock, self.seconds, self.appear_after = actions, clock, seconds, appear_after
        self.appear_at = 0.0
        self.ref_at, self.cancels_at = None, []

    def __call__(self, args):
        self.clock.t += self.seconds
        if any(a.endswith("git/refs") for a in args):
            self.ref_at = self.clock.t
        if any(a.endswith("/dispatches") for a in args):
            self.appear_at = self.clock.t + self.appear_after
        if any(a.endswith("/cancel") for a in args):
            self.cancels_at.append(self.clock.t)
        if any("workflows/tests.yml/runs" in a for a in args) and self.clock.t < self.appear_at:
            return json.dumps({"workflow_runs": []})
        return self.actions(args)


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
                           settings=TestSettings(github_tests=True), clock=Clock())


def github(world, **kwargs):
    kwargs.setdefault("oses", ("linux", "windows"))
    kwargs.setdefault("gh", world.actions)
    kwargs.setdefault("sleep", world.clock.sleep)
    kwargs.setdefault("clock", world.clock.now)
    return GithubPlace(repo=REPO, store=world.store, settings=world.settings, **kwargs)


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
    world.settings = DEFAULTS  # the defaults: github_tests on
    seed(world.store, "own", 5.0)
    seed(world.store, "github", 1.0)
    done = run(world)
    assert (done.place, done.outcome) == ("github", "passed")
    [start] = starts(world.store)
    assert (start["place"], start["chosen"], start["reason"]) == ("github", "github", "earliest_finish")
    assert "github" in [o["place"] for o in start["places_offered"]]
    assert offered(start) == {"place": "github", "expected_minutes": 1.0, "dropped": None,
                              "note": f"0 busy of {DEFAULTS.github_capacity}"}


def test_with_own_faster_the_rule_picks_own_and_places_offered_still_lists_github(world):
    world.settings = DEFAULTS  # the defaults: github_tests on
    seed(world.store, "own", 1.0)
    seed(world.store, "github", 5.0)
    done = run(world)
    assert done.place == "own"
    [start] = starts(world.store)
    assert (start["place"], start["reason"]) == ("own", "earliest_finish")
    assert "github" in [o["place"] for o in start["places_offered"]]
    assert offered(start)["expected_minutes"] == 5.0 and offered(start)["dropped"] is None
    assert world.actions.calls and not any("dispatches" in " ".join(c) for c in world.actions.calls)


def test_with_the_default_settings_and_actions_on_github_is_offered(world):
    seed(world.store, "own", 5.0)
    seed(world.store, "github", 1.0)
    done = run(world, settings=DEFAULTS)
    assert done.place == "github"
    [start] = starts(world.store)
    assert "github" in [o["place"] for o in start["places_offered"]]
    assert offered(start) == {"place": "github", "expected_minutes": 1.0, "dropped": None,
                              "note": f"0 busy of {DEFAULTS.github_capacity}"}


def test_the_default_places_build_the_github_place_with_run_gh_by_default_and_none_when_off(tmp_path, monkeypatch):
    calls = []

    def fake_gh(args):  # the fake gh the production path takes, never the real one
        calls.append(args)
        return "{}"

    monkeypatch.setattr(runner, "run_gh", fake_gh)
    store, work = Store.open(tmp_path / "store"), Repos(tmp_path).caller
    places = default_places(DEFAULTS, work, repo=REPO, store=store)
    assert places["github"].gh is fake_gh and calls == []
    assert "github" not in default_places(TestSettings(github_tests=False), work, repo=REPO, store=store)


def test_a_github_place_built_with_no_gh_argument_reaches_no_real_gh(world):
    """The autouse guard in conftest refuses a real gh start at the process call, so a place built with no
    `gh` argument reads GitHub as unreadable (off) and starts nothing."""
    entry = GithubPlace(REPO, world.store, DEFAULTS).availability(NOW)
    assert entry.on is False


def test_github_stays_switched_off_while_the_setting_github_tests_is_false(world):
    done = run(world, settings=TestSettings(github_tests=False))
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
    waited = QUEUE_GRACE_SECONDS + 60  # `hold` adds a minute: past the grace, inside the queue bound, while 8 run: the limit is 8
    assert waited + 60 < DEFAULT_QUEUE_BOUND_MINUTES * 60
    world.actions.hold(OTHER, 8, queued=2, queued_for=waited)
    run(world)
    start = starts(world.store)[0]
    assert offered(start)["note"] == "8 busy of 8" and offered(start)["dropped"] == "busy"


def test_a_job_that_only_just_queued_does_not_make_the_runs_show_a_limit(world):
    world.actions.hold(OTHER, 8, queued=2, queued_for=2)
    run(world)
    assert offered(starts(world.store)[0])["note"] == "8 busy of 20"


def test_the_busy_count_pages_through_every_run_of_the_account(world):
    for _ in range(130):
        world.actions.hold_jobs(OTHER, ["ubuntu-latest"])
    run(world)
    assert offered(starts(world.store)[0])["note"] == "130 busy of 20"


def test_the_busy_count_pages_through_every_job_of_a_run(world):
    world.actions.hold_jobs(OTHER, ["windows-latest"] * 130)
    run(world)
    assert offered(starts(world.store)[0])["note"] == "130 busy of 20"


def test_only_github_hosted_linux_and_windows_jobs_are_machines_of_the_limit(world):
    world.actions.hold_jobs(OTHER, ["ubuntu-24.04", "ubuntu-latest", "windows-2022", "windows-latest"])
    world.actions.hold_jobs(OTHER, ["macos-latest", "macos-14", ["self-hosted", "linux", "x64"],
                                    ["self-hosted", "windows"], ["self-hosted"]])
    run(world)
    assert offered(starts(world.store)[0])["note"] == "4 busy of 20"


def test_a_queued_self_hosted_or_macos_job_does_not_make_the_runs_show_a_limit(world):
    world.actions.hold(OTHER, 5)
    world.actions.hold_jobs(OTHER, ["macos-latest", ["self-hosted", "linux"]], status="queued", queued_for=300)
    run(world)
    assert offered(starts(world.store)[0])["note"] == "5 busy of 20"


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


def test_one_dispatched_run_feeds_the_rule_one_time_per_kind_queued_to_finished(world):
    # two OS jobs, two kinds of time on file for them; the rule reads one of each, the run's own
    world.actions.dispatch_jobs = {LINUX_CHECK: DispatchedJob(queued=0, started=130, completed=400),
                                   WINDOWS_CHECK: DispatchedJob(label="windows-latest", queued=20, started=60,
                                                                completed=380)}
    run(world, forced="github")
    _, measured = place_rule.read_facts(world.store, repo=REPO, selection="full", need_os="any", now=NOW)
    github_runs, github_starts = measured["github"].runs, measured["github"].starts
    assert len(github_runs) == 1 and abs(github_runs[0] * 60 - 400) <= 5
    assert len(github_starts) == 1 and github_starts[0] == 0
    # the importer's own events for the run (selection "nightly", a dispatch) carry no time of theirs
    _, nightly = place_rule.read_facts(world.store, repo=REPO, selection="nightly", need_os="any", now=NOW)
    assert nightly["github"].runs == ()


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


def test_a_request_that_selects_ids_is_refused_before_anything_is_made_on_github(world, tmp_path):
    # the dispatch runs the whole workflow; a run of chosen ids recorded under that selection would be a false one
    request = Request(world.repos.caller, world.repos.old, ("pytest",), {}, 900.0, ["tests/test_a.py::test_one"])
    with pytest.raises(PlaceRefused) as refused:
        github(world).run(request, tmp_path / "scratch")
    assert refused.value.code == "place_off"
    assert world.actions.calls == [] and world.actions.refs == {}


def test_a_run_of_selected_ids_is_a_refused_run_and_the_rule_goes_to_another_place(world, tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("tests/test_a.py::test_one" + chr(10), "utf-8")
    done = run_tests(world.store, work_tree=world.repos.caller, repo=REPO, settings=world.settings, ids_file=ids,
                     places={"own": StubOwn(), "github": github(world)}, place="github", now=lambda: NOW,
                     sleep=lambda seconds: None)
    assert (done.outcome, done.reason) == ("refused", "place_off")
    assert not any("dispatches" in " ".join(c) for c in world.actions.calls)


# --- the bound counts the time that passed, the calls' as well as the sleeps -----------------------------

def test_the_bound_counts_the_seconds_gh_calls_take_not_only_the_sleeps(world):
    world.actions.dispatch_jobs = {LINUX_CHECK: DispatchedJob(completed=None)}  # never ends
    world.settings = TestSettings(github_tests=True, bound_minutes=1)
    slow = SlowGh(world.actions, world.clock, 15)
    done = run(world, github(world, gh=slow), forced="github")
    assert (done.outcome, done.reason) == ("no_result", "cut")
    [cancelled_at] = slow.cancels_at
    assert cancelled_at <= slow.ref_at + 60 + 15 + 15  # the bound, the call that found it passed, the cancel call


def test_a_run_that_appears_only_after_the_bound_is_looked_for_once_more_and_cancelled(world):
    world.settings = TestSettings(github_tests=True, bound_minutes=1)
    # shown first to the look made after the bound: the budget read each pause makes (#414 row 10) moves the looks
    # by 15 s, so the run is listed from 70 s after its dispatch, not 60 s
    slow = SlowGh(world.actions, world.clock, 15, appear_after=70)
    done = run(world, github(world, gh=slow), forced="github")
    assert (done.outcome, done.reason) == ("no_result", "cut")
    assert world.actions.cancelled == [world.actions.runs[REPO][0]["id"]]


def test_a_run_that_never_appears_is_cut_and_nothing_is_cancelled(world):
    world.settings = TestSettings(github_tests=True, bound_minutes=1)
    slow = SlowGh(world.actions, world.clock, 15, appear_after=10_000)
    done = run(world, github(world, gh=slow), forced="github")
    assert (done.outcome, done.reason) == ("no_result", "cut")
    assert world.actions.cancelled == []


# --- the poll waits longer while the request budget is under the threshold (#414 decision 9, row 10) -----

def sleeping(world):
    """A sleep that records each pause and moves the test clock on, as a real pause would."""

    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        world.clock.sleep(seconds)

    return sleeps, sleep


def test_the_github_poll_is_the_base_while_the_budget_is_above_the_threshold(world):
    world.actions.core = (2500, 5000)  # half left: above the 20% threshold
    assert github(world).poll_wait() == 20.0
    world.settings = TestSettings(github_tests=True, bound_minutes=1)
    world.actions.dispatch_jobs = {LINUX_CHECK: DispatchedJob(completed=None)}  # never ends: the polls run to the bound
    sleeps, sleep = sleeping(world)
    run(world, github(world, sleep=sleep), forced="github")
    assert sleeps and set(sleeps) == {20.0}


def test_the_github_poll_grows_while_the_budget_is_under_the_threshold(world):
    world.actions.core = (500, 5000)  # a tenth left: 1 + floor(log2(1000 / 500)) = 2 doublings of 20 s
    assert github(world).poll_wait() == 80.0
    world.settings = TestSettings(github_tests=True, bound_minutes=2)
    world.actions.dispatch_jobs = {LINUX_CHECK: DispatchedJob(completed=None)}
    sleeps, sleep = sleeping(world)
    run(world, github(world, sleep=sleep), forced="github")
    assert sleeps[0] == 80.0  # the first pause is the grown interval, then the bound's rest
