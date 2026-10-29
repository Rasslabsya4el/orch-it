"""The workflow watch's decision (#411 unit 2): a non-green latest run opens one watch ticket, a repeat
non-green run comments on it, a green run closes it. The decision is one pure function: no I/O here.
Test data uses invented names only (`example-flow`, repo `acme/example`)."""

import pytest

from orch_it import tickets
from orch_it.merge import workflow_watch as watch
from orch_it.tickets.reader import TRIAGE_LABEL

REPO = "acme/example"
SKIP = frozenset({"tests"})


def run(workflow="example-flow", run_id=4117, outcome="failure", jobs=3):
    url = f"https://github.com/{REPO}/actions/runs/{run_id}"
    return watch.WorkflowRun(workflow, run_id, url, "2026-10-28T09:00:00Z", outcome, jobs)


def decide(latest, issues=(), skip=SKIP):
    return watch.decide(list(latest), list(issues), skip=skip)


def opened(actions):
    return [action for action in actions if isinstance(action, watch.Open)]


@pytest.mark.parametrize("outcome, jobs, phrase", [
    ("failure", 3, "failure"),
    ("cancelled", 3, "cancelled"),
    ("timed_out", 3, "timed out"),
    ("other", 0, "no job started"),
], ids=["failure", "cancelled", "timed_out", "no_job_started"])
def test_a_non_green_run_opens_one_watch_ticket(outcome, jobs, phrase):
    actions = decide([run(outcome=outcome, jobs=jobs)])
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    action = actions[0]
    assert action.workflow == "example-flow"
    assert action.title == "Workflow example-flow did not end green"
    assert "example-flow" in action.body
    assert f"https://github.com/{REPO}/actions/runs/4117" in action.body
    assert "4117" in action.body
    assert phrase in action.body


def test_a_second_non_green_run_comments_and_opens_none():
    actions = decide([run(run_id=4118)], [watch.WatchIssue(7, "example-flow", last_run_id=4117)])
    assert opened(actions) == []
    assert len(actions) == 1 and isinstance(actions[0], watch.Comment)
    assert actions[0].issue_number == 7
    assert actions[0].run.run_id == 4118


def test_the_same_run_again_does_nothing():
    actions = decide([run(run_id=4117)], [watch.WatchIssue(7, "example-flow", last_run_id=4117)])
    assert actions == []


@pytest.mark.parametrize("outcome, jobs", [
    ("success", 3),
    ("skipped", 2),
    ("other", 2),
], ids=["success", "skipped_with_jobs", "other_with_jobs"])
def test_a_green_run_closes_the_watch(outcome, jobs):
    actions = decide([run(outcome=outcome, jobs=jobs)], [watch.WatchIssue(7, "example-flow", last_run_id=4117)])
    assert len(actions) == 1 and isinstance(actions[0], watch.Close)
    assert actions[0].issue_number == 7


def test_a_green_run_with_no_watch_does_nothing():
    assert decide([run(outcome="success")]) == []


def test_the_skipped_workflow_does_nothing_with_or_without_a_watch():
    failing = run(workflow="tests", outcome="failure")
    assert decide([failing], skip=SKIP) == []
    assert decide([failing], [watch.WatchIssue(7, "tests", last_run_id=None)], skip=SKIP) == []


def test_six_workflows_with_two_non_green_open_two_watch_tickets():
    latest = [
        run(workflow="example-flow", outcome="success"),
        run(workflow="nightly-example", outcome="success"),
        run(workflow="docs-site", outcome="skipped", jobs=1),
        run(workflow="release-cut", outcome="timed_out", run_id=4201),
        run(workflow="moonlight-sync", outcome="failure", run_id=4202),
        run(workflow="lint-only", outcome="success"),
    ]
    actions = decide(latest)
    assert [action.workflow for action in opened(actions)] == ["release-cut", "moonlight-sync"]
    assert len(actions) == 2


def test_several_watches_of_one_workflow_comment_on_the_lowest_number_only():
    issues = [watch.WatchIssue(9, "example-flow", last_run_id=4000),
              watch.WatchIssue(7, "example-flow", last_run_id=4000)]
    actions = decide([run(run_id=4118)], issues)
    assert len(actions) == 1 and isinstance(actions[0], watch.Comment)
    assert actions[0].issue_number == 7


@pytest.mark.parametrize("outcome, jobs", [
    ("failure", 3),
    ("cancelled", 3),
    ("timed_out", 3),
    ("other", 0),
], ids=["failure", "cancelled", "timed_out", "no_job_started"])
def test_the_watch_body_keeps_the_shape_with_no_violations(outcome, jobs):
    action = decide([run(workflow="nightly-example", outcome=outcome, jobs=jobs)])[0]
    ticket = tickets.parse(action.body, title=action.title, labels=watch.LABELS)
    assert tickets.violations(ticket) == []
    assert watch.LABELS == (TRIAGE_LABEL,)
