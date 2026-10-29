"""Row 5 live (`tests/merge_trial_live_r5.py`) on fakes only: no network, no real sleep. The fake trial lists the
runs of `main-check.yml` the way GitHub would after each `tests` run on the base (a push, a rerun): each one is
completed with the conclusion the case scripts and does what the workflow does (`check_main`), or what the case
scripts for it."""

import importlib

import merge_trial
import pytest
from fake_github import REPO
from test_merge_trial import FakeTrial, RedByFiles, World

from orch_it.merge.main_check import check_main

MAIN = merge_trial.MAIN_CHECK_WORKFLOW


class LiveTrial(FakeTrial):
    """The fake trial with what the live row asks of the adapter: the default branch it reads, and the runs GitHub
    lists for a workflow file."""

    def __init__(self, hub, lock_root, listings, default="main"):
        super().__init__(hub, lock_root)
        self.listings, self.default = listings, default

    def preflight(self):
        return {"default_branch": self.default, "base_is_default": self.default == self.base}

    def workflow_runs(self, file):
        return list(self.listings.get(file, []))

    def dispatch_workflow(self, file):
        self.workflow_log.append(("dispatch", file))


class LiveWorld(World):
    """Row 5's world with `main-check.yml` on. After each push of the base and each rerun of its `tests` run, a
    main-check run is listed, completed, with the (conclusion, act) the script gives it, in order (the last one
    repeats nothing: after the script, every run is ("success", "check")). `act`: `check` runs the check,
    `duplicate` runs it and opens a second red ticket of the same tests, `none` does nothing."""

    def __init__(self, tmp_path, script=(), default="main"):
        super().__init__(tmp_path)
        self.listings: dict[str, list[dict]] = {}
        self.script, self.counter = list(script), 0
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
            check_main(self.hub, self.store, REPO, "main")
        if conclusion == "success" and act == "duplicate":
            first = self.hub.issue(REPO, self.hub.issues_with_label(REPO, "priority")[0])
            self.hub.create_issue(REPO, first.title + " (again)", first.body, ["ready-for-agent", "priority"])
        stamp = self.hub.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.listings.setdefault(MAIN, []).insert(0, {
            "id": run_id, "created_at": stamp, "status": "completed", "conclusion": conclusion,
            "event": "workflow_run", "head_sha": sha})


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
