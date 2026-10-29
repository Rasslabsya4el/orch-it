"""The live form of row 4 of the merge trial (`tests/merge_trial_live_r4.py`): the five pull requests of
`merge_trial.row_4` with the repo's `follow` workflow left on. Fakes only: no network, no real sleep. The fake
GitHub plays the workflows: each event `follow.yml` runs on (a pull request closed or merged, a comment on a pull
request) appends a completed run to the listing `workflow_runs` gives for that file, and runs the real `follow` on
that pull request, as the workflow's step does. The red end is moved by the `reconcile` workflow, which the harness
dispatches: the fake appends a run of it and follows every open pull request, as the sweep does. The harness itself
never calls `follow` (a case below makes it raise).
"""

import importlib
import itertools
from datetime import timedelta

import merge_trial
import pytest
from fake_github import PASSED, REPO
from test_merge_trial import World

from orch_it.merge.follow import follow
from orch_it.testruns.github import GithubError

OWNER = REPO.split("/")[0]
ENDS = ("merged", "after_merge", "red", "closed_unmerged", "comment")
FOLLOW = merge_trial.FOLLOW_WORKFLOW
RECONCILE = merge_trial.RECONCILE_WORKFLOW
BOUND_MINUTES = 20  # the bound of every wait in the cases (fake time: each poll is 30 s of it)


@pytest.fixture
def live():
    """The module under test, imported here so that each case is red on its own until it exists."""
    return importlib.import_module("merge_trial_live_r4")


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


class FollowWorkflow:
    """GitHub's `follow` and `reconcile` workflows in the fake. `conclusions`: event -> the run's conclusion (default
    success), the events being `pull_request`, `issue_comment` and `workflow_dispatch`; `silent`: events whose run
    never starts; `no_effect`: events whose run is listed but whose work does not run; `dispatch_fails`: the dispatch
    of `reconcile` is refused. `runs`: workflow file -> its listing, newest first, the shape of the gh JSON the
    harness reads; `dispatched`: the files the harness dispatched."""

    def __init__(self, world, *, conclusions=None, silent=(), no_effect=(), dispatch_fails=False):
        self.world, self.hub = world, world.hub
        self.conclusions = dict(conclusions or {})
        self.silent, self.no_effect = set(silent), set(no_effect)
        self.dispatch_fails = dispatch_fails
        self.runs: dict[str, list[dict]] = {FOLLOW: [], RECONCILE: []}
        self.dispatched: list[str] = []
        self.ids = itertools.count(1)
        world.trial.workflow_runs = self.listing  # the trial's listing of a workflow file
        world.trial.dispatch_workflow = self.dispatch  # the trial's dispatch of a workflow file
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
        return [dict(run) for run in self.runs[file]]

    def fire(self, number, event):
        if number not in self.hub.pulls:
            return
        head = self.hub.branches["main"][-1] if event == "issue_comment" else self.hub.head(number)
        self._run(FOLLOW, event, head, lambda: self._follow(number))

    def dispatch(self, file):
        self.dispatched.append(file)
        if file != RECONCILE:
            raise AssertionError(f"the live row dispatches only {RECONCILE}, not {file}")
        if self.dispatch_fails:
            raise GithubError("gh_failed", f"{REPO}: the dispatch of {file} is refused")
        self._run(file, "workflow_dispatch", self.hub.branches["main"][-1], self._sweep)

    def _run(self, file, event, head, effect):
        if event in self.silent:
            return
        self.runs[file].insert(0, {"id": str(next(self.ids)), "created_at": self.hub.now.isoformat(),
                                   "status": "completed", "conclusion": self.conclusions.get(event, "success"),
                                   "event": event, "head_sha": head})
        if event not in self.no_effect:
            effect()

    def _follow(self, number):
        follow(self.hub, self.world.store, REPO, number, base="main", settings=merge_trial.Plan().settings,
               now=self.hub.now, owner=OWNER)

    def _sweep(self):
        for number, pull in list(self.hub.pulls.items()):
            if pull.state == "open":
                self._follow(number)


def run_row(live, world, **plan):
    plan = {"wait_minutes": BOUND_MINUTES, **plan}
    return live.row_4_live(world.trial, world.rows, world.store, world.clock, world.sleep, merge_trial.Plan(**plan))


def test_all_five_ends_handled_by_successful_runs_pass_and_the_harness_never_calls_follow(
        live, world, monkeypatch):
    flow = FollowWorkflow(world)

    def harness_follow(*args, **kwargs):
        raise AssertionError("the live row must not call follow itself")

    monkeypatch.setattr(merge_trial, "follow", harness_follow)
    row = run_row(live, world)

    assert row["passed"], {end: row[end] for end in ENDS if not row[end]["passed"]}
    assert set(row) >= {"follow_workflow_disabled", "passed", *ENDS}
    assert row["follow_workflow_disabled"] is False and world.trial.workflow_log == []  # left on, never touched
    assert flow.dispatched == [RECONCILE]
    for end in ENDS:
        assert row[end]["store"] == "not read (live)"
    for end in ("merged", "after_merge", "closed_unmerged", "comment"):
        assert row[end]["handled_by"] == "follow"
        assert row[end]["follow"] == "success" and row[end]["follow_run"] is not None
    assert row["comment"]["follow_run"] == next(r["id"] for r in flow.runs[FOLLOW] if r["event"] == "issue_comment")
    assert row["red"]["handled_by"] == "reconcile" and row["red"]["follow"] == "success"
    assert row["red"]["follow_run"] == flow.runs[RECONCILE][0]["id"]
    assert [p for p in world.hub.pulls.values() if p.state == "open"] == []  # every opened pull request closed


def test_the_red_end_moved_by_a_successful_reconcile_run_passes(live, world):
    FollowWorkflow(world)
    row = run_row(live, world)

    assert row["red"]["passed"] is True and row["red"]["error"] is None
    assert row["red"]["handled_by"] == "reconcile"
    assert row["passed"] is True


def test_a_follow_run_that_ends_failure_fails_its_end_and_its_run_id_is_in_the_record(live, world):
    flow = FollowWorkflow(world, conclusions={"issue_comment": "failure"})
    row = run_row(live, world)

    run_id = next(r["id"] for r in flow.runs[FOLLOW] if r["event"] == "issue_comment")
    assert row["comment"]["passed"] is False and row["comment"]["follow"] == "failure"
    assert row["comment"]["follow_run"] == run_id and run_id in row["comment"]["error"]
    assert row["passed"] is False
    assert all(row[end]["passed"] for end in ENDS if end != "comment")


def test_an_end_whose_follow_run_never_starts_is_a_failed_end_with_its_reason_at_the_bound(live, world):
    FollowWorkflow(world, silent={"issue_comment"})
    start = world.clock()
    row = run_row(live, world)

    assert row["comment"]["passed"] is False and row["comment"]["follow_run"] is None
    assert "not within" in row["comment"]["error"] and FOLLOW in row["comment"]["error"]
    assert world.clock() - start >= timedelta(minutes=BOUND_MINUTES)
    assert row["passed"] is False and row["merged"]["passed"]  # the other ends still run and are judged


def test_a_successful_follow_run_whose_ticket_is_not_where_its_end_puts_it_fails_the_end(live, world):
    FollowWorkflow(world, no_effect={"issue_comment"})
    row = run_row(live, world)

    assert row["comment"]["follow"] == "success" and row["comment"]["passed"] is False
    assert row["comment"]["needs_triage"] is False  # nothing moved the ticket
    assert row["passed"] is False and row["merged"]["passed"]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped"])
def test_a_reconcile_run_that_does_not_end_success_fails_the_red_end_with_its_run_id(live, world, conclusion):
    flow = FollowWorkflow(world, conclusions={"workflow_dispatch": conclusion})
    row = run_row(live, world)

    run_id = flow.runs[RECONCILE][0]["id"]
    assert row["red"]["passed"] is False and row["red"]["follow"] == conclusion
    assert row["red"]["follow_run"] == run_id and run_id in row["red"]["error"]
    assert row["red"]["handled_by"] == "reconcile"
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "red")


def test_a_reconcile_dispatch_that_is_refused_fails_the_red_end_with_its_reason(live, world):
    flow = FollowWorkflow(world, dispatch_fails=True)
    row = run_row(live, world)

    assert flow.dispatched == [RECONCILE]
    assert row["red"]["passed"] is False and row["red"]["follow_run"] is None
    assert row["red"]["handled_by"] == "reconcile"
    assert "refused" in row["red"]["error"] and RECONCILE in row["red"]["error"]
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "red")


def test_a_reconcile_run_that_never_starts_fails_the_red_end_at_the_bound(live, world):
    FollowWorkflow(world, silent={"workflow_dispatch"})
    start = world.clock()
    row = run_row(live, world)

    assert row["red"]["passed"] is False and row["red"]["follow_run"] is None
    assert "not within" in row["red"]["error"] and RECONCILE in row["red"]["error"]
    assert world.clock() - start >= timedelta(minutes=BOUND_MINUTES)
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "red")


def test_a_successful_reconcile_run_that_leaves_the_red_ticket_not_moved_fails_the_red_end(live, world):
    FollowWorkflow(world, no_effect={"workflow_dispatch"})
    row = run_row(live, world)

    assert row["red"]["follow"] == "success" and row["red"]["error"] is None
    assert row["red"]["passed"] is False  # the ticket is not where the red end puts it
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "red")


def test_the_registry_returns_the_live_row_4_for_row_4(live, monkeypatch):
    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {})

    assert merge_trial.live_row_function(4) is live.row_4_live
