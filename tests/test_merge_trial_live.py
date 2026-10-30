"""The live form of the merge trial (#411 unit 11a): the bounded wait of the label list, `--live` and its
registry, and the shared steps of the live rows (`tests/merge_trial_live.py`). Fakes only: no network, no
real sleep; a fake clock advances on every sleep."""

import importlib
import json
import runpy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import merge_trial
import pytest

from orch_it.merge import red_main
from orch_it.merge.hub import PASSED, Check
from required_names import LINUX_CHECK, WINDOWS_CHECK

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
REPO = "owner/trial"
HEAD = "c" * 40
RED_IDS = frozenset({"tests/test_trial_main_red_x.py::test_red_on_a_push"})
MAIN = merge_trial.MAIN_CHECK_WORKFLOW
DEFAULT_BRANCH = "main"


@pytest.fixture
def live():
    """The module under test, imported here so that each case is red on its own until it exists."""
    return importlib.import_module("merge_trial_live")


class Clock:
    """`clock()` gives the fake time; `sleep` moves it on and counts what it slept."""

    def __init__(self):
        self.now, self.slept = NOW, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


class Hub:
    """The listings of the hub the cases read: the label list (one answer per read, the last one repeats),
    the issues by number, and the check runs by head sha."""

    def __init__(self, label_reads=(), issues=None, checks=None):
        self.label_reads, self.reads = list(label_reads), 0
        self.issues, self.by_sha = dict(issues or {}), dict(checks or {})

    def issues_with_label(self, repo, label):
        self.reads += 1
        answer = self.label_reads.pop(0) if len(self.label_reads) > 1 else self.label_reads[0]
        return list(answer)

    def issue(self, repo, number):
        return self.issues.get(number)

    def checks(self, repo, sha, *, statuses=False):
        return list(self.by_sha.get(sha, []))


class WorkflowTrial:
    """A trial that lists the runs of a workflow file (one listing per read, the last one repeats) and
    records each dispatch."""

    repo, base = REPO, "main"

    def __init__(self, listings=()):
        self.listings, self.reads, self.dispatched = list(listings), 0, []

    def workflow_runs(self, file):
        self.reads += 1
        return self.listings.pop(0) if len(self.listings) > 1 else self.listings[0]

    def dispatch_workflow(self, file):
        self.dispatched.append(file)


class MainTrial:
    """The trial `main` needs around the rows: preflight, cleanup, restore and the rate limit."""

    repo, base = REPO, "main"

    def __init__(self):
        self.restored, self.cleaned, self.rate_calls = False, False, 0

    def preflight(self):
        return {"fake": True}

    def cleanup(self):
        self.cleaned = True
        return {"tickets": [], "pulls": [], "errors": []}

    def restore(self):
        self.restored = True

    def rate_remaining(self):
        self.rate_calls += 1
        return 4321


def make_run(trial, hub, clock, plan=None, live=False):
    return merge_trial._Run(trial or WorkflowTrial(), hub, None, clock, clock.sleep, plan, "t", live=live)


def red_ticket_body(ids=RED_IDS):
    _, body = red_main.build(sorted(ids), sha="a" * 40, run_id="1", pr=None)
    return body


def listed(run_id, created, *, status="completed", conclusion="success", event="pull_request", sha=HEAD, branch=None):
    return {"id": run_id, "created_at": created, "status": status, "conclusion": conclusion, "event": event,
            "head_sha": sha, "head_branch": branch}


def world_factory(trial, clock):
    return lambda args: (trial, None, clock, clock.sleep)


# --- the bounded wait of the label list -----------------------------------------------------------------------


def test_red_tickets_of_returns_once_the_ticket_appears_after_two_empty_reads():
    clock = Clock()
    issue = SimpleNamespace(open=True, body=red_ticket_body())
    hub = Hub(label_reads=[[], [], [7]], issues={7: issue})
    run = make_run(None, hub, clock)

    assert run.red_tickets_of(RED_IDS) == [7]
    assert hub.reads == 3
    assert clock.slept == [run.settings.poll_seconds, run.settings.poll_seconds]


def test_red_tickets_of_returns_the_list_as_read_when_the_bound_passes_and_no_ticket_came():
    # the spec: a wait ends when the ticket appears "or the bound passes"; past the bound the caller gets the
    # list as read (row 5's first check then records `red_tickets: []` and fails with its numbers)
    clock = Clock()
    hub = Hub(label_reads=[[]])
    plan = merge_trial.Plan(list_minutes=3)
    run = make_run(None, hub, clock, plan)
    start = clock.now

    assert run.red_tickets_of(RED_IDS) == []
    assert clock.now - start >= timedelta(minutes=3)
    assert hub.reads >= 2


def test_red_tickets_of_the_settle_form_reads_once_after_a_fixed_settle_and_returns_what_it_reads():
    clock = Clock()
    hub = Hub(label_reads=[[]])
    run = make_run(None, hub, clock)

    assert run.red_tickets_of(RED_IDS, settle=True) == []
    assert hub.reads == 1
    assert clock.slept == [merge_trial.SETTLE_SECONDS]


def test_the_list_bound_is_a_plan_field_with_a_default_of_three_minutes():
    assert merge_trial.Plan().list_minutes == 3


def test_the_run_carries_live_and_it_is_off_by_default():
    clock = Clock()
    assert make_run(None, Hub(), clock).live is False
    assert make_run(None, Hub(), clock, live=True).live is True


# --- --live and the registry ------------------------------------------------------------------------------------


def test_live_is_off_by_default_and_the_flag_turns_it_on(tmp_path):
    base = ["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path)]
    assert merge_trial.parse_args(base).live is False
    assert merge_trial.parse_args([*base, "--live"]).live is True


def test_the_help_of_live_says_row_5_live_needs_allow_default_base(capsys):
    with pytest.raises(SystemExit):
        merge_trial.parse_args(["--help"])
    text = " ".join(capsys.readouterr().out.split())

    assert "row 5 live needs --allow-default-base" in text


def test_live_takes_each_row_from_the_registry_and_writes_the_rate_limit_into_the_report(tmp_path, monkeypatch):
    clock, trial, calls = Clock(), MainTrial(), []

    def live_row(trial_, hub, store, clock_, sleep, plan=None):
        calls.append(plan)
        return {"passed": True}

    def row_function(*args, **kwargs):
        raise AssertionError("the row of ROW_FUNCTIONS must not run with --live")

    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {4: live_row})
    monkeypatch.setitem(merge_trial.ROW_FUNCTIONS, 4, row_function)
    out = tmp_path / "out.json"
    code = merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "4",
                             "--live", "--store", str(tmp_path / "store"), "--out", str(out)],
                            world=world_factory(trial, clock))

    report = json.loads(out.read_text("utf-8"))
    assert code == 0 and len(calls) == 1
    assert report["rows"]["4"] == {"passed": True}
    assert report["rate_limit"] == 4321 and trial.rate_calls == 1


def test_without_live_the_row_comes_from_row_functions_as_today(tmp_path, monkeypatch):
    clock, trial, called = Clock(), MainTrial(), []

    def row_function(*args, **kwargs):
        called.append("row")
        return {"passed": True}

    def live_row(*args, **kwargs):
        called.append("live")
        return {"passed": True}

    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {4: live_row})
    monkeypatch.setitem(merge_trial.ROW_FUNCTIONS, 4, row_function)
    merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "4",
                      "--store", str(tmp_path / "store"), "--out", str(tmp_path / "out.json")],
                     world=world_factory(trial, clock))

    assert called == ["row"]


def test_a_live_row_whose_module_is_missing_is_a_trial_error_naming_it(tmp_path, monkeypatch):
    # a module name that never exists, so the case stays green when the real live modules are added
    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {})
    monkeypatch.setitem(merge_trial.LIVE_ROW_MODULES, 5, "merge_trial_live_never_there")
    with pytest.raises(merge_trial.TrialError, match="merge_trial_live_never_there"):
        merge_trial.live_row_function(5)


def test_main_with_live_records_a_missing_live_row_as_its_error_and_the_run_goes_on(tmp_path, monkeypatch):
    clock, trial = Clock(), MainTrial()
    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {})
    monkeypatch.setitem(merge_trial.LIVE_ROW_MODULES, 5, "merge_trial_live_never_there")
    out = tmp_path / "out.json"
    code = merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "5",
                             "--live", "--store", str(tmp_path / "store"), "--out", str(out)],
                            world=world_factory(trial, clock))

    report = json.loads(out.read_text("utf-8"))
    assert code == 1 and report["rows"]["5"]["passed"] is False
    assert "merge_trial_live_never_there" in report["rows"]["5"]["error"]


# --- the shared steps of the live rows --------------------------------------------------------------------------


def test_wait_workflow_run_returns_the_run_once_it_ended(live):
    clock = Clock()
    trial = WorkflowTrial([
        [listed("1", "2026-10-29T10:01:00Z", status="in_progress", conclusion=None)],
        [listed("1", "2026-10-29T10:01:00Z")],
    ])
    hub = Hub(checks={HEAD: [Check(LINUX_CHECK, PASSED, "1", 1, NOW), Check(WINDOWS_CHECK, PASSED, "1", 1, NOW)]})
    found = live.wait_workflow_run(make_run(trial, hub, clock), "follow.yml", NOW)

    assert found == live.LiveRun(run_id="1", conclusion="success", event="pull_request", head_sha=HEAD,
                                 jobs_started=2)
    assert trial.reads == 2


def test_wait_workflow_run_ignores_a_run_created_before_since(live):
    clock = Clock()
    trial = WorkflowTrial([[listed("1", "2026-10-29T09:00:00Z"), listed("2", "2026-10-29T10:02:00Z")]])
    found = live.wait_workflow_run(make_run(trial, Hub(), clock), "follow.yml", NOW)

    assert found.run_id == "2"


def test_wait_workflow_run_compares_created_at_in_whole_seconds_so_a_run_of_the_same_second_is_found(live):
    # GitHub's created_at has whole seconds only: a `since` of 10:00:00.5 still finds a run created at 10:00:00
    clock = Clock()
    since = datetime(2026, 10, 29, 10, 0, 0, 500000, tzinfo=UTC)
    trial = WorkflowTrial([[listed("8", "2026-10-29T10:00:00Z")]])
    found = live.wait_workflow_run(make_run(trial, Hub(), clock), "follow.yml", since, minutes=1)

    assert found.run_id == "8"


def test_a_skipped_run_has_no_started_jobs_even_when_its_checks_carry_a_start(live):
    clock = Clock()
    trial = WorkflowTrial([[listed("7", "2026-10-29T10:01:00Z", conclusion="skipped")]])
    hub = Hub(checks={HEAD: [Check(LINUX_CHECK, PASSED, "7", 1, NOW), Check(WINDOWS_CHECK, PASSED, "7", 1, NOW)]})
    found = live.wait_workflow_run(make_run(trial, hub, clock), "follow.yml", NOW)

    assert found.conclusion == "skipped" and found.jobs_started == 0


def test_wait_workflow_run_returns_a_skipped_run_as_skipped_and_does_not_wait(live):
    clock = Clock()
    trial = WorkflowTrial([[listed("3", "2026-10-29T10:01:00Z", conclusion="skipped")]])
    found = live.wait_workflow_run(make_run(trial, Hub(), clock), "follow.yml", NOW)

    assert found.conclusion == "skipped" and found.jobs_started == 0
    assert clock.slept == []


def test_wait_workflow_run_takes_only_the_run_with_the_head_sha_and_the_event_asked_for(live):
    clock = Clock()
    trial = WorkflowTrial([[
        listed("4", "2026-10-29T10:01:00Z", event="push"),
        listed("5", "2026-10-29T10:01:00Z", sha="d" * 40),
        listed("6", "2026-10-29T10:01:00Z"),
    ]])
    found = live.wait_workflow_run(make_run(trial, Hub(), clock), "follow.yml", NOW, head_sha=HEAD,
                                   event="pull_request")

    assert found.run_id == "6"


def test_wait_workflow_run_with_a_branch_takes_the_base_run_over_a_skipped_run_of_a_pull_request_branch(live):
    # a pull request's own test run starts a skipped run of the same workflow inside the same window; it is listed
    # above the base's run and has ended, so without `branch` it would be the one taken
    clock = Clock()
    trial = WorkflowTrial([[
        listed("11", "2026-10-29T10:01:30Z", event="workflow_run", conclusion="skipped", branch="trial-pr"),
        listed("12", "2026-10-29T10:01:00Z", event="workflow_run", branch=DEFAULT_BRANCH),
    ]])
    found = live.wait_workflow_run(make_run(trial, Hub(), clock), MAIN, NOW, event="workflow_run",
                                   branch=DEFAULT_BRANCH)

    assert found.run_id == "12" and found.conclusion == "success"


def test_wait_workflow_run_with_a_branch_returns_a_skipped_run_of_that_branch_as_skipped(live):
    clock = Clock()
    trial = WorkflowTrial([[
        listed("13", "2026-10-29T10:01:00Z", event="workflow_run", conclusion="skipped", branch=DEFAULT_BRANCH),
    ]])
    found = live.wait_workflow_run(make_run(trial, Hub(), clock), MAIN, NOW, event="workflow_run",
                                   branch=DEFAULT_BRANCH)

    assert found.run_id == "13" and found.conclusion == "skipped"
    assert clock.slept == []


def test_wait_workflow_run_without_a_branch_keeps_taking_any_branch(live):
    clock = Clock()
    trial = WorkflowTrial([[
        listed("14", "2026-10-29T10:01:30Z", event="workflow_run", conclusion="skipped", branch="trial-pr"),
    ]])
    found = live.wait_workflow_run(make_run(trial, Hub(), clock), MAIN, NOW, event="workflow_run")

    assert found.run_id == "14"


def test_wait_workflow_run_times_out_with_trial_timeout_when_no_run_ends(live):
    clock = Clock()
    trial = WorkflowTrial([[listed("1", "2026-10-29T10:01:00Z", status="in_progress", conclusion=None)]])

    with pytest.raises(merge_trial.TrialTimeout):
        live.wait_workflow_run(make_run(trial, Hub(), clock), "follow.yml", NOW, minutes=1)


def test_wait_workflow_run_never_takes_an_ignored_run_even_when_it_is_listed_first(live):
    # an older skipped run is listed first: with its id ignored the end takes the run after it
    clock = Clock()
    trial = WorkflowTrial([[listed("1", "2026-10-29T10:00:00Z", conclusion="skipped"),
                            listed("2", "2026-10-29T10:00:00Z")]])
    found = live.wait_workflow_run(make_run(trial, Hub(), clock), "follow.yml", NOW, ignore_ids=frozenset({"1"}))

    assert found.run_id == "2"


def test_wait_workflow_run_waits_for_a_pending_run_over_an_older_completed_run_it_ignores(live):
    # the pending run is the end's own; the completed one before it is ignored, so the wait goes on until it ends
    clock = Clock()
    trial = WorkflowTrial([
        [listed("2", "2026-10-29T10:00:00Z", status="in_progress", conclusion=None),
         listed("1", "2026-10-29T10:00:00Z")],
        [listed("2", "2026-10-29T10:00:00Z"), listed("1", "2026-10-29T10:00:00Z")],
    ])
    found = live.wait_workflow_run(make_run(trial, Hub(), clock), "follow.yml", NOW, ignore_ids=frozenset({"1"}))

    assert found.run_id == "2" and trial.reads == 2
    assert len(clock.slept) == 1


def test_dispatch_workflow_asks_the_trial_once_for_the_file(live):
    clock = Clock()
    trial = WorkflowTrial()
    result = live.dispatch_workflow(make_run(trial, Hub(), clock), "reconcile.yml")

    assert result is None
    assert trial.dispatched == ["reconcile.yml"]


def test_the_shared_workflow_files_are_the_three_constants_of_merge_trial():
    assert (merge_trial.FOLLOW_WORKFLOW, merge_trial.MAIN_CHECK_WORKFLOW, merge_trial.RECONCILE_WORKFLOW) == (
        "follow.yml", "main-check.yml", "reconcile.yml")


# --- the real adapter's gh calls -----------------------------------------------------------------------------


class Gh:
    def __init__(self, answer=""):
        self.calls, self.answer = [], answer

    def __call__(self, args):
        self.calls.append(list(args))
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


def test_the_real_trial_dispatches_a_workflow_with_gh_on_its_repo_and_base(tmp_path):
    gh = Gh()
    merge_trial.RealTrial(REPO, "main", tmp_path, None, gh=gh).dispatch_workflow("reconcile.yml")

    assert gh.calls == [["workflow", "run", "reconcile.yml", "--repo", REPO, "--ref", "main"]]


def test_the_real_trial_lists_the_runs_of_a_workflow_file_with_one_gh_read(tmp_path):
    gh = Gh({"total_count": 1, "workflow_runs": [listed("9", "2026-10-29T10:01:00Z")]})
    runs = merge_trial.RealTrial(REPO, "main", tmp_path, None, gh=gh).workflow_runs("follow.yml")

    assert runs == [listed("9", "2026-10-29T10:01:00Z")]
    assert gh.calls == [["api", f"repos/{REPO}/actions/workflows/follow.yml/runs?per_page=30"]]


def test_the_real_trial_reads_the_core_rate_limit_with_one_gh_call(tmp_path):
    gh = Gh({"resources": {"core": {"limit": 5000, "remaining": 4321}}})

    assert merge_trial.RealTrial(REPO, "main", tmp_path, None, gh=gh).rate_remaining() == 4321
    assert gh.calls == [["api", "rate_limit"]]


def test_a_live_module_without_its_row_function_is_a_trial_error_naming_the_module(monkeypatch):
    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {})
    monkeypatch.setitem(merge_trial.LIVE_ROW_MODULES, 5, "test_merge_trial_live")
    with pytest.raises(merge_trial.TrialError, match="test_merge_trial_live"):
        merge_trial.live_row_function(5)


def test_a_listing_that_is_not_json_is_a_gh_error_the_wait_tries_again(tmp_path):
    gh = Gh("not json")
    with pytest.raises(merge_trial.GithubError):
        merge_trial.RealTrial(REPO, "main", tmp_path, None, gh=gh).workflow_runs("follow.yml")


# --- tests/merge_trial.py run as a script ------------------------------------------------------------------------

SCRIPT = Path(__file__).with_name("merge_trial.py")


def test_run_as_a_script_registers_itself_as_merge_trial_so_the_live_module_shares_its_classes(monkeypatch):
    # the script runs as __main__; the module it registers as `merge_trial` must be the one the live module
    # imports, or TrialError and _Run would exist twice
    monkeypatch.setitem(sys.modules, "merge_trial", None)
    monkeypatch.setitem(sys.modules, "merge_trial_live", None)
    monkeypatch.delitem(sys.modules, "merge_trial_live")
    monkeypatch.setattr(sys, "argv", ["merge_trial.py", "--help"])
    with pytest.raises(SystemExit):
        runpy.run_path(str(SCRIPT), run_name="__main__")
    script = sys.modules["merge_trial"]

    assert script is not None and script.__name__ == "__main__"
    live = importlib.import_module("merge_trial_live")
    assert live.merge_trial is script
    assert live.merge_trial.TrialError is script.TrialError
