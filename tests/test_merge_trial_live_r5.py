"""Row 5 live (`tests/merge_trial_live_r5.py`) on fakes only: no network, no real sleep. The fake trial lists the runs
of the main-check workflow the way GitHub would after each run of the test workflow on the base (a push, a rerun):
each one is completed with the conclusion the case scripts and does what the workflow does (`check_main`), or what
the case scripts for it. With `pr_noise`, each base run is listed under a skipped run of a pull request's own branch,
the way a pull request's own test run starts one inside the same window."""

import importlib
from datetime import UTC, datetime
from types import SimpleNamespace

import merge_trial
import pytest
from fake_github import REPO
from test_merge_trial import FakeTrial, RedByFiles, World

from orch_it.merge.main_check import check_main

MAIN = merge_trial.MAIN_CHECK_WORKFLOW
DEFAULT_BRANCH = "main"
PR_BRANCH = "trial-pr-noise"
STALE_SHA = "5eed0001" + "0" * 32  # the base head before the merge: a skipped run on the base's branch carries it


class LiveTrial(FakeTrial):
    """The fake trial with what the live row asks of the adapter: the default branch its preflight read (the row
    takes it from `default_branch`, never from another preflight) and the runs GitHub lists for a workflow file."""

    def __init__(self, hub, lock_root, listings, default=DEFAULT_BRANCH):
        super().__init__(hub, lock_root)
        self.listings, self.default_branch = listings, default
        self.preflight_calls = 0

    def preflight(self):
        self.preflight_calls += 1
        return {"default_branch": self.default_branch, "base_is_default": self.default_branch == self.base}

    def workflow_runs(self, file):
        return list(self.listings.get(file, []))

    def dispatch_workflow(self, file):
        self.workflow_log.append(("dispatch", file))


class LiveWorld(World):
    """Row 5's world with the main-check workflow on. After each push of the base and each rerun of its test run, a
    main-check run is listed, completed, with the (conclusion, act) the script gives it, in order (the last one
    repeats nothing: after the script, every run is ("success", "check")). `act`: `check` runs the check,
    `duplicate` runs it and opens a second red ticket of the same tests, `none` does nothing. With `pr_noise`, a
    skipped run of the pull request branch `PR_BRANCH` is listed above each run of the base."""

    def __init__(self, tmp_path, script=(), default=DEFAULT_BRANCH, pr_noise=False, stale_noise=False):
        super().__init__(tmp_path)
        self.listings: dict[str, list[dict]] = {}
        self.script, self.counter, self.pr_noise, self.stale_noise = list(script), 0, pr_noise, stale_noise
        self.trial = LiveTrial(self.hub, tmp_path / "locks", self.listings, default)
        self.ci.red = RedByFiles(self.hub, self.trial)
        self.hub.on_merge.append(self._push_main_check)
        rerun = self.hub.rerun

        def rerun_and_check(repo, run_id):
            sha = self.hub.runs_by_id[run_id].sha
            rerun(repo, run_id)
            self._main_check(sha)

        self.hub.rerun = rerun_and_check

    def _push_main_check(self, number):
        self._main_check(self.hub.pulls[number].merge_sha)

    def _main_check(self, sha):
        conclusion, act = self.script.pop(0) if self.script else ("success", "check")
        self.counter += 1
        run_id = f"main-check-{self.counter}"
        if conclusion == "success" and act in ("check", "duplicate"):
            check_main(self.hub, self.store, REPO, DEFAULT_BRANCH)
        if conclusion == "success" and act == "duplicate":
            first = self.hub.issue(REPO, self.hub.issues_with_label(REPO, "priority")[0])
            self.hub.create_issue(REPO, first.title + " (again)", first.body, ["ready-for-agent", "priority"])
        stamp = self.hub.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        runs = self.listings.setdefault(MAIN, [])
        runs.insert(0, {"id": run_id, "created_at": stamp, "status": "completed", "conclusion": conclusion,
                        "event": "workflow_run", "head_sha": sha, "head_branch": DEFAULT_BRANCH})
        if self.pr_noise:  # listed above the base's run, the way a pull request's own test run starts one
            runs.insert(0, {"id": f"pr-check-{self.counter}", "created_at": stamp, "status": "completed",
                            "conclusion": "skipped", "event": "workflow_run", "head_sha": sha,
                            "head_branch": PR_BRANCH})
        if self.stale_noise:  # the live case of #411: a skipped run on the base's branch with the base head from before
            # the merge, created in the same window and listed above the base's run (the fix pull request's own run)
            runs.insert(0, {"id": f"stale-check-{self.counter}", "created_at": stamp, "status": "completed",
                            "conclusion": "skipped", "event": "workflow_run", "head_sha": STALE_SHA,
                            "head_branch": DEFAULT_BRANCH})


@pytest.fixture
def row5():
    """The live row, imported here so that each case is red on its own until the module exists."""
    return importlib.import_module("merge_trial_live_r5").row_5_live


def test_the_happy_path_with_three_successful_main_check_runs_passes_and_leaves_the_workflow_on(row5, tmp_path):
    world = LiveWorld(tmp_path)
    out = world.run(row5)

    assert out["passed"] is True, out
    assert [r["conclusion"] for r in out["main_check_runs"]] == ["success", "success", "success"]
    first, second, third = out["first_check"], out["second_check"], out["third_check"]
    assert len(first["red_tickets"]) == 1 and first["shape_violations"] == 0 and first["names_the_pull_request"]
    assert second["opened"] == [] and second["red_tickets"] == first["red_tickets"]
    assert third["closed"] == first["red_tickets"]
    assert world.trial.workflow_log == []  # never disabled, never enabled: the main-check workflow stays on
    [number] = first["red_tickets"]
    assert not world.hub.issues[(REPO, number)].open


def test_the_row_takes_the_default_branch_from_the_report_and_does_not_read_the_repo_again(row5, tmp_path):
    # the CLI's preflight already read the default branch into the report: the row asks the repo nothing for it
    world = LiveWorld(tmp_path)
    out = world.run(row5)

    assert out["passed"] is True
    assert world.trial.preflight_calls == 0


def test_a_skipped_run_of_a_pull_request_branch_is_not_the_bases_and_the_base_run_is_taken(row5, tmp_path):
    # each step's window holds the base's run and a skipped run of the pull request branch: only the base's counts
    world = LiveWorld(tmp_path, pr_noise=True)
    out = world.run(row5)

    assert out["passed"] is True, out
    assert [r["run_id"] for r in out["main_check_runs"]] == ["main-check-1", "main-check-2", "main-check-3"]
    assert [r["conclusion"] for r in out["main_check_runs"]] == ["success", "success", "success"]


def test_only_a_skipped_base_run_fails_the_step_even_with_a_pull_request_run_listed_above_it(row5, tmp_path):
    world = LiveWorld(tmp_path, script=[("skipped", "none")], pr_noise=True)
    out = world.run(row5)

    assert out["passed"] is False
    assert out["failed_step"] == {"step": "first", "run_id": "main-check-1", "conclusion": "skipped"}
    assert out["main_check_runs"] == [{"step": "first", "run_id": "main-check-1", "conclusion": "skipped"}]
    assert "second_check" not in out and "third_check" not in out


def test_a_skipped_base_branch_run_with_an_old_head_sha_is_not_the_steps_run_and_the_merge_sha_run_is_taken(row5,
                                                                                                          tmp_path):
    # #411: the fix pull request's own run is a skipped run on the base's branch with the base head from before the
    # merge, created in the same window and listed above the base's run: each step takes the run with its merge sha
    world = LiveWorld(tmp_path, stale_noise=True)
    out = world.run(row5)

    assert out["passed"] is True, out
    assert [r["run_id"] for r in out["main_check_runs"]] == ["main-check-1", "main-check-2", "main-check-3"]
    assert [r["conclusion"] for r in out["main_check_runs"]] == ["success", "success", "success"]


def test_the_only_run_with_the_merge_sha_skipped_fails_its_step_beside_an_old_sha_run(row5, tmp_path):
    world = LiveWorld(tmp_path, script=[("skipped", "none")], stale_noise=True)
    out = world.run(row5)

    assert out["passed"] is False
    assert out["failed_step"] == {"step": "first", "run_id": "main-check-1", "conclusion": "skipped"}
    assert out["main_check_runs"] == [{"step": "first", "run_id": "main-check-1", "conclusion": "skipped"}]
    assert "second_check" not in out and "third_check" not in out


def test_a_step_that_ends_without_its_head_sha_is_an_error_and_waits_for_no_run(tmp_path):
    # no silent fallback to "any run": a step whose block set no sha raises before any run is looked for
    module = importlib.import_module("merge_trial_live_r5")
    run = SimpleNamespace(clock=lambda: datetime.now(UTC))
    out = {"main_check_runs": []}

    with pytest.raises(merge_trial.TrialError, match="head sha"):
        with module._main_check_step(run, out, "first"):
            pass
    assert out["main_check_runs"] == []


def test_a_main_check_run_that_ends_skipped_is_not_a_pass_and_the_step_fails(row5, tmp_path):
    world = LiveWorld(tmp_path, script=[("skipped", "none")])
    out = world.run(row5)

    assert out["passed"] is False
    assert out["failed_step"]["conclusion"] == "skipped" and out["failed_step"]["run_id"] == "main-check-1"
    assert out["main_check_runs"] == [{"step": "first", "run_id": "main-check-1", "conclusion": "skipped"}]
    assert "second_check" not in out and "third_check" not in out
    assert world.hub.issues_with_label(REPO, "priority") == []


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_a_main_check_run_that_ends_in_failure_or_cancelled_fails_the_step_with_its_run_id(row5, tmp_path, conclusion):
    world = LiveWorld(tmp_path, script=[(conclusion, "none")])
    out = world.run(row5)

    assert out["passed"] is False
    assert out["failed_step"]["run_id"] == "main-check-1" and out["failed_step"]["conclusion"] == conclusion


def test_a_second_red_ticket_opened_after_the_rerun_fails_the_row(row5, tmp_path):
    world = LiveWorld(tmp_path, script=[("success", "check"), ("success", "duplicate"), ("success", "check")])
    out = world.run(row5)

    assert out["passed"] is False
    assert len(out["second_check"]["opened"]) == 1 and len(out["second_check"]["red_tickets"]) == 2


def test_the_ticket_not_closed_after_the_green_run_fails_the_row(row5, tmp_path):
    world = LiveWorld(tmp_path, script=[("success", "check"), ("success", "check"), ("success", "none")])
    out = world.run(row5)

    assert out["passed"] is False
    assert out["third_check"]["closed"] == []
    assert len(world.hub.issues_with_label(REPO, "priority")) == 1  # still open, as the fake holds it


def test_a_base_that_is_not_the_default_branch_is_refused_by_name(row5, tmp_path):
    world = LiveWorld(tmp_path, default="trunk")

    with pytest.raises(merge_trial.TrialError, match="to be the default branch of .*trunk"):
        world.run(row5)
    assert world.trial.workflow_log == []  # refused before any step: nothing was pushed or turned off


def test_live_row_function_five_is_the_row_5_live_of_the_live_module(monkeypatch):
    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {})
    module = importlib.import_module("merge_trial_live_r5")

    assert merge_trial.live_row_function(5) is module.row_5_live
    assert merge_trial.LIVE_ROW_MODULES[5] == "merge_trial_live_r5"
