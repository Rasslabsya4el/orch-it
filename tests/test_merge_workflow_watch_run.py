"""The workflow watch's sweep (#411 unit 3): `watch_workflows` reads from GitHub the latest ended run of every
workflow it lists and the open watch tickets, decides with `workflow_watch.decide` and carries out the actions.
Test data uses invented names only (`example-flow`, `other-flow`, repo `acme/example`), on FakeGitHub; the real
`GhPullHub` reads are checked once against a scripted `gh`."""

import json

import pytest
from fake_github import FakeGitHub

from orch_it import tickets
from orch_it.merge import workflow_watch as watch
from orch_it.merge.gh import GhPullHub
from orch_it.merge.workflow_watch_run import checked_names, run_mark, watch_workflows
from orch_it.tickets.reader import TRIAGE_LABEL

REPO = "acme/example"
URL = f"https://github.com/{REPO}/actions/runs/4117"


@pytest.fixture
def hub():
    return FakeGitHub()


def watched(hub):
    return hub.issues_with_label(REPO, TRIAGE_LABEL)


def sweep(hub):
    return watch_workflows(hub, REPO, checked_names(hub, REPO))


def test_a_failing_run_opens_one_needs_triage_issue_with_the_workflow_mark_and_the_run_mark(hub):
    hub.end_run("example-flow", 4117, "failure")
    actions = sweep(hub)
    assert [type(action) for action in actions] == [watch.Open]
    assert len(watched(hub)) == 1
    issue = hub.issues[(REPO, watched(hub)[0])]
    assert issue.title == "Workflow example-flow did not end green"
    assert issue.labels == (TRIAGE_LABEL,)
    lines = issue.body.split("\n")
    assert lines[0] == watch.mark("example-flow")
    assert lines[1] == run_mark(4117)
    assert watch.workflow_of(issue.body) == "example-flow"
    assert tickets.violations(tickets.parse(issue.body, title=issue.title, labels=watch.LABELS)) == []


def test_a_second_non_green_run_comments_and_the_same_run_again_adds_nothing(hub):
    hub.end_run("example-flow", 4117, "failure")
    sweep(hub)
    number = watched(hub)[0]
    hub.end_run("example-flow", 4118, "failure")
    actions = sweep(hub)
    assert [type(action) for action in actions] == [watch.Comment]
    assert watched(hub) == [number]
    assert len(hub.notes[(REPO, number)]) == 1
    assert hub.notes[(REPO, number)][0].body.split("\n")[0] == run_mark(4118)
    assert sweep(hub) == []
    assert len(hub.notes[(REPO, number)]) == 1


def test_the_next_green_run_closes_the_watch(hub):
    hub.end_run("example-flow", 4117, "failure")
    sweep(hub)
    number = watched(hub)[0]
    hub.end_run("example-flow", 4119, "success")
    actions = sweep(hub)
    assert [type(action) for action in actions] == [watch.Close]
    assert watched(hub) == []
    closed = hub.issues[(REPO, number)]
    assert not closed.open and closed.state_reason == "completed"
    assert hub.notes[(REPO, number)][-1].body.split("\n")[0] == run_mark(4119)


@pytest.mark.parametrize("outcome", ["failure", "success"])
def test_a_workflow_whose_latest_run_started_no_job_opens_one(hub, outcome):
    hub.end_run("example-flow", 4120, outcome, jobs=0)
    sweep(hub)
    assert len(watched(hub)) == 1
    assert "no job started" in hub.issues[(REPO, watched(hub)[0])].body


def test_the_tests_workflow_non_green_run_opens_nothing_here(hub):
    hub.end_run("example-tests", 4500, "failure", path=".github/workflows/tests.yml")
    assert checked_names(hub, REPO) == frozenset({"example-tests"})
    assert sweep(hub) == []
    assert watched(hub) == []


def test_six_workflows_with_two_non_green_open_two_issues_and_a_name_no_code_knows_works(hub):
    hub.end_run("example-flow", 4101, "success")
    hub.end_run("other-flow", 4102, "success")
    hub.end_run("moonlight-sync", 4103, "failure")
    hub.end_run("", 4104, "timed_out", path=".github/workflows/x.yml")
    hub.end_run("docs-site", 4105, "skipped", jobs=1)
    hub.end_run("example-tests", 4106, "success", path=".github/workflows/tests.yml")
    actions = sweep(hub)
    assert sorted(action.workflow for action in actions) == [".github/workflows/x.yml", "moonlight-sync"]
    assert len(watched(hub)) == 2
    assert sorted(watch.workflow_of(hub.issues[(REPO, n)].body) for n in watched(hub)) == [
        ".github/workflows/x.yml", "moonlight-sync"]


def test_the_real_hub_reads_the_listed_workflows_their_latest_run_and_the_watch_tickets():
    def scripted_gh(args):
        text = " ".join(args)
        if "actions/workflows?" in text:
            return json.dumps({"total_count": 2, "workflows": [
                {"id": 1, "name": "example-flow", "path": ".github/workflows/example-flow.yml"},
                {"id": 2, "name": None, "path": ".github/workflows/x.yml"}]})
        if "actions/workflows/1/runs?" in text:
            return json.dumps({"total_count": 1, "workflow_runs": [
                {"id": 4117, "status": "completed", "conclusion": "failure", "updated_at": "2026-10-28T09:00:00Z",
                 "html_url": URL}]})
        if "actions/workflows/2/runs?" in text:
            return json.dumps({"total_count": 0, "workflow_runs": []})
        if "actions/runs/4117/jobs" in text:
            return json.dumps({"total_count": 2, "jobs": [{"started_at": "2026-10-28T08:59:00Z"},
                                                          {"started_at": None}]})
        if "/issues/7/comments" in text:
            return json.dumps([{"user": {"login": "owner", "type": "User"}, "body": run_mark(4120),
                                "created_at": "2026-10-28T10:00:00Z"}])
        if "/issues?labels=needs-triage" in text:
            return json.dumps([
                {"number": 7, "title": "t", "body": f"{watch.mark('example-flow')}\n{run_mark(4116)}\n",
                 "labels": [{"name": TRIAGE_LABEL}], "state": "open"},
                {"number": 8, "title": "t", "body": "no mark", "labels": [{"name": TRIAGE_LABEL}], "state": "open"}])
        raise AssertionError(f"no answer for {args}")

    hub = GhPullHub(gh=scripted_gh)
    assert hub.latest_workflow_runs(REPO) == [
        watch.WorkflowRun("example-flow", 4117, URL, "2026-10-28T09:00:00Z", "failure", 1)]
    assert hub.workflow_files(REPO) == {"example-flow": ".github/workflows/example-flow.yml",
                                        ".github/workflows/x.yml": ".github/workflows/x.yml"}
    assert hub.watch_issues(REPO) == [watch.WatchIssue(7, "example-flow", 4120)]
