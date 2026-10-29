"""The live form of row 4 of the merge trial (`tests/merge_trial_live_r4.py`): the five pull requests of
`merge_trial.row_4` with the repo's follow workflow left on. Fakes only: no network, no real sleep. The fake GitHub
plays the workflows: every write the hub takes (a comment, the owner's or the layer's) and every pull request closed
or merged starts a run of the follow workflow's file, and a run of a pull request's follow runs the real `follow` on
it, as the workflow's step does. The red end is moved by the reconcile workflow, which the harness dispatches: the fake
appends a run of it and follows the open pull requests, as the sweep does. The harness itself never calls `follow`
(a case below makes it raise).
"""

import importlib
import itertools
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

import merge_trial
import pytest
from fake_github import PASSED, REPO
from test_merge_trial import World

from orch_it.merge.follow import follow
from orch_it.testruns.github import GithubError

OWNER = REPO.split("/")[0]
BASE = "main"
ENDS = ("merged", "after_merge", "red", "closed_unmerged", "comment")
FOLLOW = merge_trial.FOLLOW_WORKFLOW
RECONCILE = merge_trial.RECONCILE_WORKFLOW
PERSON = "Wait, please"  # the start of the owner's comment the comment end writes
BOUND_MINUTES = 20  # the bound of every wait in the cases (fake time: each poll is 30 s of it)


@pytest.fixture
def live():
    """The module under test, imported here so that each case is red on its own until it exists."""
    return importlib.import_module("merge_trial_live_r4")


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


class FollowWorkflow:
    """The follow and reconcile workflows in the fake, started by what the hub writes (every write goes through
    `say`) and by the events of the row. A pull request closed or merged starts a run that follows it; a comment on a
    pull request starts an `issue_comment` run that follows it; a comment on a ticket starts an `issue_comment` run
    whose job is skipped. A write the workflow itself makes starts nothing (its token). `conclusions`: event -> the
    run's conclusion (default success); `silent`: events whose run never starts; `no_effect`: events whose run is
    listed but whose work does not run; `holds`: a comment's text prefix -> the listings its run stays in progress
    for; `dispatch_fails`: the dispatch of reconcile is refused. `runs`: workflow file -> its listing, newest first,
    the shape of the gh JSON the harness reads (`_reads` and `_text` are the fake's own); `dispatched`: the files the
    harness dispatched."""

    def __init__(self, world, *, conclusions=None, silent=(), no_effect=(), holds=None, dispatch_fails=False):
        self.world, self.hub = world, world.hub
        self.conclusions = dict(conclusions or {})
        self.silent, self.no_effect = set(silent), set(no_effect)
        self.holds = dict(holds or {})
        self.dispatch_fails = dispatch_fails
        self.inside = False  # the workflow's own step is running: its writes start no run
        self.runs: dict[str, list[dict]] = {FOLLOW: [], RECONCILE: []}
        self.dispatched: list[str] = []
        self.ids = itertools.count(1)
        world.trial.workflow_runs = self.listing  # the trial's listing of a workflow file
        world.trial.dispatch_workflow = self.dispatch  # the trial's dispatch of a workflow file
        self.hub.on_merge.append(self._pull_event)
        said, close_pull = self.hub.say, self.hub.close_pull

        def written(number, body, **kwargs):
            said(number, body, **kwargs)
            self._comment_event(number, body)

        def closed(repo_or_number, number=None):
            close_pull(repo_or_number, number)
            self._pull_event(repo_or_number if number is None else number)

        self.hub.say, self.hub.close_pull = written, closed

    def listing(self, file):
        out = []
        for run in self.runs[file]:
            if run["_reads"] > 0:
                run["_reads"] -= 1
                run["status"] = "in_progress" if run["_reads"] else "completed"
            out.append({key: value for key, value in run.items() if not key.startswith("_")})
        return out

    def dispatch(self, file):
        self.dispatched.append(file)
        if file != RECONCILE:
            raise AssertionError(f"the live row dispatches only {RECONCILE}, not {file}")
        if self.dispatch_fails:
            raise GithubError("gh_failed", f"{REPO}: the dispatch of {file} is refused")
        self._run(RECONCILE, "workflow_dispatch", self.hub.branches[BASE][-1], effect=self._sweep)

    def _pull_event(self, number):
        if number in self.hub.pulls:
            self._run(FOLLOW, "pull_request", self.hub.head(number), effect=lambda: self._follow(number))

    def _comment_event(self, number, body):
        if self.inside:
            return
        head = self.hub.branches[BASE][-1]  # an issue_comment run is on the default branch
        reads = next((n for prefix, n in self.holds.items() if body.startswith(prefix)), 0)
        if number in self.hub.pulls:
            self._run(FOLLOW, "issue_comment", head, effect=lambda: self._follow(number), reads=reads, text=body)
        else:
            self._run(FOLLOW, "issue_comment", head, conclusion="skipped", reads=reads, text=body)

    def _run(self, file, event, head, *, conclusion=None, effect=None, reads=0, text=""):
        if event in self.silent:
            return
        self.runs[file].insert(0, {"id": str(next(self.ids)), "created_at": self.hub.now.isoformat(),
                                   "status": "in_progress" if reads else "completed",
                                   "conclusion": conclusion or self.conclusions.get(event, "success"),
                                   "event": event, "head_sha": head, "_reads": reads, "_text": text})
        if effect is not None and event not in self.no_effect:
            effect()

    def _follow(self, number):
        self.inside = True
        try:
            follow(self.hub, self.world.store, REPO, number, base=BASE, settings=merge_trial.Plan().settings,
                   now=self.hub.now, owner=OWNER)
        finally:
            self.inside = False

    def _sweep(self):
        """The sweep follows the parked pull requests and the merge line's members; the fake follows every open pull
        request, which here are those (`reconcile._to_follow`)."""
        for number, pull in list(self.hub.pulls.items()):
            if pull.state == "open":
                self._follow(number)


def person_run(flow):
    """The id of the run of the owner's comment that the comment end writes (its text is the row's)."""
    return next(r["id"] for r in flow.runs[FOLLOW] if r["_text"].startswith(PERSON))


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
        assert datetime.fromisoformat(row[end]["since"]).tzinfo is not None
    for end in ("merged", "after_merge", "closed_unmerged"):
        assert row[end]["handled_by"] == "follow" and row[end]["run_conclusion"] == "success"
        assert row[end]["run_id"] is not None
    assert row["comment"]["handled_by"] == "follow" and row["comment"]["run_conclusion"] == "success"
    assert row["comment"]["run_id"] == person_run(flow)
    assert row["red"]["handled_by"] == "reconcile" and row["red"]["run_conclusion"] == "success"
    assert row["red"]["run_id"] == flow.runs[RECONCILE][0]["id"]
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

    run_id = person_run(flow)
    assert row["comment"]["passed"] is False and row["comment"]["run_conclusion"] == "failure"
    assert row["comment"]["run_id"] == run_id and run_id in row["comment"]["error"]
    assert row["passed"] is False
    assert all(row[end]["passed"] for end in ENDS if end != "comment")


def test_an_end_whose_follow_run_never_starts_is_a_failed_end_with_its_reason_at_the_bound(live, world):
    FollowWorkflow(world, silent={"issue_comment"})
    start = world.clock()
    row = run_row(live, world)

    assert row["comment"]["passed"] is False and row["comment"]["run_id"] is None
    assert "not within" in row["comment"]["error"] and FOLLOW in row["comment"]["error"]
    assert world.clock() - start >= timedelta(minutes=BOUND_MINUTES)
    assert row["passed"] is False and row["merged"]["passed"]  # the other ends still run and are judged


def test_a_successful_follow_run_whose_ticket_is_not_where_its_end_puts_it_fails_the_end(live, world):
    FollowWorkflow(world, no_effect={"issue_comment"})
    row = run_row(live, world)

    assert row["comment"]["run_conclusion"] == "success" and row["comment"]["passed"] is False
    assert row["comment"]["needs_triage"] is False  # nothing moved the ticket
    assert row["passed"] is False and row["merged"]["passed"]


def test_the_comment_end_waits_for_its_own_pending_run_not_an_older_completed_one(live, world):
    # the marks `pr open` wrote before the end are completed runs listed before its `since`; the owner's comment's
    # run is in progress for two listings: the end must wait for that run, never take an older one
    flow = FollowWorkflow(world, holds={PERSON: 2})
    row = run_row(live, world)

    assert row["comment"]["passed"], row["comment"]["error"]
    assert row["comment"]["run_id"] == person_run(flow)
    assert row["passed"] is True


def test_the_comment_end_waits_for_the_marks_of_its_pull_request_to_settle_before_it_starts(live, world):
    # the layer's marks are in progress when the end starts: the end's own run is still the owner's comment's one
    flow = FollowWorkflow(world, holds={"<!--": 2})
    row = run_row(live, world)

    assert row["comment"]["passed"], row["comment"]["error"]
    assert row["comment"]["run_id"] == person_run(flow)


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped"])
def test_a_reconcile_run_that_does_not_end_success_fails_the_red_end_with_its_run_id(live, world, conclusion):
    flow = FollowWorkflow(world, conclusions={"workflow_dispatch": conclusion})
    row = run_row(live, world)

    run_id = flow.runs[RECONCILE][0]["id"]
    assert row["red"]["passed"] is False and row["red"]["run_conclusion"] == conclusion
    assert row["red"]["run_id"] == run_id and run_id in row["red"]["error"]
    assert row["red"]["handled_by"] == "reconcile"
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "red")


def test_a_reconcile_dispatch_that_is_refused_fails_the_red_end_with_its_reason(live, world):
    flow = FollowWorkflow(world, dispatch_fails=True)
    row = run_row(live, world)

    assert flow.dispatched == [RECONCILE]
    assert row["red"]["passed"] is False and row["red"]["run_id"] is None
    assert row["red"]["handled_by"] == "reconcile"
    assert "refused" in row["red"]["error"] and RECONCILE in row["red"]["error"]
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "red")


def test_a_reconcile_run_that_never_starts_fails_the_red_end_at_the_bound(live, world):
    FollowWorkflow(world, silent={"workflow_dispatch"})
    start = world.clock()
    row = run_row(live, world)

    assert row["red"]["passed"] is False and row["red"]["run_id"] is None
    assert "not within" in row["red"]["error"] and RECONCILE in row["red"]["error"]
    assert world.clock() - start >= timedelta(minutes=BOUND_MINUTES)
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "red")


def test_a_successful_reconcile_run_that_leaves_the_red_ticket_not_moved_fails_the_red_end(live, world):
    FollowWorkflow(world, no_effect={"workflow_dispatch"})
    row = run_row(live, world)

    assert row["red"]["run_conclusion"] == "success" and row["red"]["error"] is None
    assert row["red"]["passed"] is False  # the ticket is not where the red end puts it
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "red")


PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
MIN_SLOW_SECONDS = 100  # the settle wait (about a minute) and the follow run of the comment both fit in the test run


def record_pull_requests(monkeypatch, wrap=None):
    """Each `_Run.pull_request` call by its label: the files it was given. `wrap(self, label, made)` runs after it."""

    made, original = {}, merge_trial._Run.pull_request

    def recording(self, label, files, **kwargs):
        made[label] = files
        result = original(self, label, files, **kwargs)
        if wrap is not None:
            wrap(self, label, result)
        return result

    monkeypatch.setattr(merge_trial._Run, "pull_request", recording)
    return made


def test_the_comment_pull_request_carries_an_unmarked_slow_test_within_the_change_run_bound(live, world, monkeypatch):
    FollowWorkflow(world)
    made = record_pull_requests(monkeypatch)
    row = run_row(live, world)

    slow = [path for path in made["comment"] if path.startswith("tests/test_trial_slow_")]
    assert len(slow) == 1, sorted(made["comment"])
    body = made["comment"][slow[0]]
    assert "pytest.mark" not in body  # no marker: the change run (`-m not serial`, the slow and live left out) runs it
    bound = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["orch_it"]["tests"]["per_change_timeout_seconds"]
    assert MIN_SLOW_SECONDS <= live.SLOW_SECONDS < bound, (live.SLOW_SECONDS, bound)  # the per-test bound is not hit
    assert f"time.sleep({live.SLOW_SECONDS})" in body
    assert row["comment"]["passed"] is True and row["passed"] is True


def test_a_comment_end_whose_pull_request_merged_before_the_comment_fails_the_end(live, world, monkeypatch):
    FollowWorkflow(world)

    def merge_before_the_comment(self, label, made):
        if label != "comment":
            return
        pr = made[2].pr
        head = world.hub.pull(REPO, pr).head_sha
        world.hub.checks_on(head)  # the required checks green on the head: the merge's own condition
        sha = world.hub.merge(REPO, pr, head, "squash")
        assert sha is not None, "the trial's precondition: the comment pull request lands before the comment"

    record_pull_requests(monkeypatch, wrap=merge_before_the_comment)
    row = run_row(live, world)

    assert row["comment"]["pr_state"] == "merged"
    assert row["comment"]["passed"] is False
    assert row["passed"] is False and all(row[end]["passed"] for end in ENDS if end != "comment")


def test_the_registry_returns_the_live_row_4_for_row_4(live, monkeypatch):
    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {})

    assert merge_trial.live_row_function(4) is live.row_4_live
