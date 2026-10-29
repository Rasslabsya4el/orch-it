"""The live form of row 4 of the merge trial (`tests/merge_trial_live_r4.py`): the five pull requests of
`merge_trial.row_4` with the repo's `follow` workflow left on. Fakes only: no network, no real sleep. The fake
GitHub plays the workflow: each event `follow.yml` runs on (a pull request closed or merged, a comment on a pull
request) appends a completed run to the listing `workflow_runs` gives, and runs the real `follow` on that pull
request, as the workflow's step does. The harness itself never calls `follow` (a case below makes it raise).
"""

import importlib
from datetime import timedelta

import merge_trial
import pytest
from fake_github import PASSED, REPO
from test_merge_trial import World

from orch_it.merge.follow import follow
from orch_it.merge.hub import FAILED, PENDING

OWNER = REPO.split("/")[0]
ENDS = ("merged", "after_merge", "red", "closed_unmerged", "comment")
BOUND_MINUTES = 20  # the bound of every wait in the cases (fake time: each poll is 30 s of it)


@pytest.fixture
def live():
    """The module under test, imported here so that each case is red on its own until it exists."""
    return importlib.import_module("merge_trial_live_r4")


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


class FollowWorkflow:
    """GitHub's `follow` workflow in the fake. `conclusions`: event -> the run's conclusion (default success);
    `silent`: events whose run never starts; `no_effect`: events whose run is listed but whose follow does not
    run; `red_fires`: a follow run when a red pull request's checks end (a trigger `follow.yml` does not have:
    the case that assumes one)."""

    def __init__(self, world, *, conclusions=None, silent=(), no_effect=(), red_fires=False):
        self.world, self.hub = world, world.hub
        self.conclusions = dict(conclusions or {})
        self.silent, self.no_effect = set(silent), set(no_effect)
        self.red_fires, self.fired_red = red_fires, set()
        self.runs: list[dict] = []  # newest first, the shape of the gh JSON the harness reads
        world.trial.workflow_runs = self.listing  # the trial's listing of a workflow file
        world.ci.on_tick.append(self._on_tick)
        self.hub.on_merge.append(lambda number: self.fire(number, "pull_request"))
        note, close_pull = self.hub.note, self.hub.close_pull

        def noted(number, body, **kwargs):
            note(number, body, **kwargs)
            self.fire(number, "issue_comment")

        def closed(repo_or_number, number=None):
            close_pull(repo_or_number, number)
            self.fire(repo_or_number if number is None else number, "pull_request")

        self.hub.note, self.hub.close_pull = noted, closed

    def listing(self, file):
        return [dict(run) for run in self.runs]

    def fire(self, number, event):
        if number not in self.hub.pulls or event in self.silent:
            return
        head = self.hub.branches["main"][-1] if event == "issue_comment" else self.hub.head(number)
        self.runs.insert(0, {"id": str(len(self.runs) + 1), "created_at": self.hub.now.isoformat(),
                             "status": "completed", "conclusion": self.conclusions.get(event, "success"),
                             "event": event, "head_sha": head})
        if event not in self.no_effect:
            follow(self.hub, self.world.store, REPO, number, base="main", settings=merge_trial.Plan().settings,
                   now=self.hub.now, owner=OWNER)

    def _on_tick(self):
        if not self.red_fires:
            return
        for number, pull in list(self.hub.pulls.items()):
            if number in self.fired_red or pull.state != "open" or not self.world.ci.red.get(number):
                continue
            states = [check.state for check in self.hub.checks(REPO, self.hub.head(number))]
            if FAILED in states and PENDING not in states:
                self.fired_red.add(number)
                self.fire(number, "check_run")


def run_row(live, world, **plan):
    plan = {"wait_minutes": BOUND_MINUTES, **plan}
    return live.row_4_live(world.trial, world.rows, world.store, world.clock, world.sleep, merge_trial.Plan(**plan))


def test_all_five_ends_handled_by_successful_follow_runs_pass_and_the_harness_never_calls_follow(
        live, world, monkeypatch):
    flow = FollowWorkflow(world, red_fires=True)

    def harness_follow(*args, **kwargs):
        raise AssertionError("the live row must not call follow itself")

    monkeypatch.setattr(merge_trial, "follow", harness_follow)
    row = run_row(live, world)

    assert row["passed"], {end: row[end] for end in ENDS if not row[end]["passed"]}
    assert set(row) >= {"follow_workflow_disabled", "passed", *ENDS}
    assert row["follow_workflow_disabled"] is False and world.trial.workflow_log == []  # left on, never touched
    for end in ENDS:
        assert row[end]["store"] == "not read (live)"
    for end in ("merged", "after_merge", "closed_unmerged", "comment"):
        assert row[end]["follow"] == "success" and row[end]["follow_run"] is not None
    assert row["comment"]["follow_run"] == next(r["id"] for r in flow.runs if r["event"] == "issue_comment")
    assert [p for p in world.hub.pulls.values() if p.state == "open"] == []  # every opened pull request closed


def test_a_follow_run_that_ends_failure_fails_its_end_and_its_run_id_is_in_the_record(live, world):
    flow = FollowWorkflow(world, conclusions={"issue_comment": "failure"}, red_fires=True)
    row = run_row(live, world)

    run_id = next(r["id"] for r in flow.runs if r["event"] == "issue_comment")
    assert row["comment"]["passed"] is False and row["comment"]["follow"] == "failure"
    assert row["comment"]["follow_run"] == run_id and run_id in row["comment"]["error"]
    assert row["passed"] is False
    assert all(row[end]["passed"] for end in ENDS if end != "comment")


def test_an_end_whose_follow_run_never_starts_is_a_failed_end_with_its_reason_at_the_bound(live, world):
    FollowWorkflow(world, silent={"issue_comment"}, red_fires=True)
    start = world.clock()
    row = run_row(live, world)

    assert row["comment"]["passed"] is False and row["comment"]["follow_run"] is None
    assert "not within" in row["comment"]["error"] and merge_trial.FOLLOW_WORKFLOW in row["comment"]["error"]
    assert world.clock() - start >= timedelta(minutes=BOUND_MINUTES)
    assert row["passed"] is False and row["merged"]["passed"]  # the other ends still run and are judged


def test_a_successful_follow_run_whose_ticket_is_not_where_its_end_puts_it_fails_the_end(live, world):
    FollowWorkflow(world, no_effect={"issue_comment"}, red_fires=True)
    row = run_row(live, world)

    assert row["comment"]["follow"] == "success" and row["comment"]["passed"] is False
    assert row["comment"]["needs_triage"] is False  # nothing moved the ticket
    assert row["passed"] is False and row["merged"]["passed"]


def test_the_red_end_with_no_event_that_moves_its_ticket_fails_with_its_reason(live, world):
    FollowWorkflow(world)  # no red trigger: a check run ending is no event the follow workflow runs on
    row = run_row(live, world)

    assert row["red"]["passed"] is False and row["red"]["follow_run"] is None
    assert "red" in row["red"]["error"] and merge_trial.FOLLOW_WORKFLOW in row["red"]["error"]
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "red")


def test_the_registry_returns_the_live_row_4_for_row_4(live, monkeypatch):
    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {})

    assert merge_trial.live_row_function(4) is live.row_4_live
