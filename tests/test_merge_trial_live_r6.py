"""Row 6, live (#411 unit 11d): the first half dispatches `reconcile` on GitHub and waits for its run, the second
half is the local sweep with Actions off. Fakes only: the `World` of `tests/test_merge_trial.py` with a trial
that lists and dispatches workflow runs; no network, no real sleep."""

import importlib
from datetime import datetime, timedelta

import merge_trial
import pytest

from test_merge_trial import FakeTrial, World

RECONCILE = merge_trial.RECONCILE_WORKFLOW


class ReconcileTrial(FakeTrial):
    """The fake trial with the two workflow calls. A dispatch is a fake `reconcile` run that is listed at once,
    ended with `conclusion`; it closes every ticket this trial made when `closes` is set. `lists` False: the
    dispatch's run never appears in the listing; `fail_dispatch`: the dispatch is refused with that GithubError."""

    def __init__(self, hub, lock_root, *, conclusion, closes, lists=True, fail_dispatch=None):
        super().__init__(hub, lock_root)
        self.conclusion, self.closes, self.lists, self.fail_dispatch = conclusion, closes, lists, fail_dispatch
        self.made: list[int] = []
        self.listed: dict[str, list[dict]] = {}
        self.events: list[tuple[str, str]] = []

    def make_ticket(self, title, body):
        number = super().make_ticket(title, body)
        self.made.append(number)
        return number

    def workflow_runs(self, file):
        self.events.append(("list", file))
        return list(self.listed.get(file, []))

    def dispatch_workflow(self, file):
        self.events.append(("dispatch", file))
        if self.fail_dispatch is not None:
            raise merge_trial.GithubError("gh_failed", self.fail_dispatch)
        if self.lists:
            runs = self.listed.setdefault(file, [])
            runs.insert(0, {"id": f"{file}-{len(self.events)}", "status": "completed",
                            "conclusion": self.conclusion, "event": "workflow_dispatch",
                            "head_sha": self.hub.branches[self.base][-1],
                            "created_at": self.hub.now.strftime("%Y-%m-%dT%H:%M:%SZ")})
        if self.closes:
            for number in self.made:
                if self.hub.issue(self.repo, number).open:
                    self.hub.close_issue(self.repo, number, "completed")


class LiveWorld(World):
    """The `World` of row 6 with a `ReconcileTrial` in place of its `FakeTrial`."""

    def __init__(self, tmp_path, *, conclusion="success", closes=True, lists=True, fail_dispatch=None):
        super().__init__(tmp_path)
        self.hub.on_merge.remove(self.trial._merged)
        self.trial = ReconcileTrial(self.hub, tmp_path / "locks", conclusion=conclusion, closes=closes,
                                    lists=lists, fail_dispatch=fail_dispatch)
        self.ci.red.trial = self.trial


@pytest.fixture
def r6():
    """The module under test, imported here so that each case is red on its own until it exists."""
    return importlib.import_module("merge_trial_live_r6")


def test_the_happy_path_closes_the_ticket_through_a_successful_reconcile_run_and_passes(r6, tmp_path):
    world = LiveWorld(tmp_path)
    out = world.run(r6.row_6_live)

    half = out["workflow_off"]
    assert out["passed"] is True and half["passed"] is True, out
    assert half["ticket_open_before"] and half["ticket_parked_before"]
    assert not half["ticket_open_after"] and half["state_reason"] == "completed"
    assert half["run_conclusion"] == "success" and half["run_id"]
    assert half["repairs"] == "not read (live)"
    assert "error" not in half


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled"])
def test_a_reconcile_run_that_did_not_succeed_fails_the_half_and_records_its_run_id(r6, tmp_path, conclusion):
    # the ticket is closed here: only the run's conclusion may fail the half
    world = LiveWorld(tmp_path, conclusion=conclusion, closes=True)
    out = world.run(r6.row_6_live)

    half = out["workflow_off"]
    assert half["passed"] is False and out["passed"] is False
    assert half["run_conclusion"] == conclusion
    assert half["run_id"] == world.trial.listed[RECONCILE][0]["id"]


def test_a_successful_reconcile_run_whose_ticket_stayed_open_fails_the_half(r6, tmp_path):
    world = LiveWorld(tmp_path, conclusion="success", closes=False)
    out = world.run(r6.row_6_live)

    half = out["workflow_off"]
    assert half["run_conclusion"] == "success"
    assert half["ticket_open_after"] is True and half["passed"] is False and out["passed"] is False


def test_the_dispatch_is_asked_exactly_once_and_before_the_wait_reads_the_runs(r6, tmp_path):
    world = LiveWorld(tmp_path)
    world.run(r6.row_6_live)

    reconcile = [event for event in world.trial.events if event[1] == RECONCILE]
    assert reconcile[0] == ("dispatch", RECONCILE)
    assert [kind for kind, _ in reconcile].count("dispatch") == 1
    assert ("list", RECONCILE) in reconcile[1:]


def test_a_timeout_of_the_wait_records_the_half_with_its_dispatch_moment_and_the_second_half_still_runs(r6, tmp_path):
    # the dispatch's run never gets listed: the wait runs out, the half is recorded as failed and row 6 goes on
    world = LiveWorld(tmp_path, lists=False)
    out = world.run(r6.row_6_live)

    half = out["workflow_off"]
    assert half["passed"] is False and out["passed"] is False
    assert RECONCILE in half["error"] and len(half["error"]) <= 200
    assert datetime.fromisoformat(half["dispatched_at"]) <= world.hub.now
    assert ("dispatch", RECONCILE) in world.trial.events
    assert out["actions_off"]["passed"] is True


def test_a_github_error_of_the_dispatch_records_the_half_with_its_dispatch_moment_and_the_second_half_still_runs(
        r6, tmp_path):
    world = LiveWorld(tmp_path, fail_dispatch="gh refused the dispatch")
    out = world.run(r6.row_6_live)

    half = out["workflow_off"]
    assert half["passed"] is False and out["passed"] is False
    assert half["error"] == "gh_failed: gh refused the dispatch"
    assert datetime.fromisoformat(half["dispatched_at"]) <= world.hub.now
    assert "run_id" not in half
    assert out["actions_off"]["passed"] is True


def test_a_reconcile_run_created_before_since_is_not_taken(r6, tmp_path):
    # an earlier reconcile run is listed, the dispatch's own run is not: the wait must not take the earlier one
    world = LiveWorld(tmp_path, closes=False, lists=False)
    earlier = world.hub.now - timedelta(hours=1)
    world.trial.listed[RECONCILE] = [{"id": "earlier", "status": "completed", "conclusion": "success",
                                      "event": "workflow_dispatch", "head_sha": world.hub.branches["main"][-1],
                                      "created_at": earlier.strftime("%Y-%m-%dT%H:%M:%SZ")}]
    out = world.run(r6.row_6_live)

    half = out["workflow_off"]
    assert half["passed"] is False and "run_id" not in half
    assert RECONCILE in half["error"]


def test_the_second_half_is_the_local_sweep_through_swept_with_actions_off(r6, tmp_path, monkeypatch):
    calls = []
    swept = merge_trial._swept

    def spy(run, label, **kwargs):
        calls.append((label, kwargs))
        return swept(run, label, **kwargs)

    monkeypatch.setattr(merge_trial, "_swept", spy)
    world = LiveWorld(tmp_path)
    out = world.run(r6.row_6_live)

    assert calls == [("actions", {"actions": False, "workflow_off": True})]
    assert out["actions_off"]["actions"] is False and out["actions_off"]["passed"] is True
    assert world.trial.actions_log == [False, True]
    assert [kind for kind, _ in world.trial.events].count("dispatch") == 1


def test_the_live_registry_gives_row_6_live_for_row_6(r6, monkeypatch):
    monkeypatch.setattr(merge_trial, "LIVE_ROW_FUNCTIONS", {})
    assert merge_trial.live_row_function(6) is r6.row_6_live
