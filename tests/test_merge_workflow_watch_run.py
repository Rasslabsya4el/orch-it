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
from orch_it.merge.workflow_watch_run import checked_names, watch_workflows
from orch_it.testruns.github import WORKFLOW_FILE
from orch_it.tickets.reader import TRIAGE_LABEL

REPO = "acme/example"
URL = f"https://github.com/{REPO}/actions/runs/4117"
FLOW_PATH = ".github/workflows/example-flow.yml"
X_PATH = ".github/workflows/x.yml"
TESTS_PATH = f".github/workflows/{WORKFLOW_FILE}"
STARTED = {"started_at": "2026-10-28T08:59:00Z", "conclusion": "failure"}
NOT_STARTED = {"started_at": None, "conclusion": None}
SKIPPED_JOB = {"started_at": "2026-10-28T09:59:00Z", "conclusion": "skipped"}


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
    assert lines[0] == watch.mark(FLOW_PATH)
    assert lines[1] == watch.run_mark(4117)
    assert watch.workflow_of(issue.body) == FLOW_PATH
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
    assert hub.notes[(REPO, number)][0].body.split("\n")[0] == watch.run_mark(4118)
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
    assert hub.notes[(REPO, number)][-1].body.split("\n")[0] == watch.run_mark(4119)


@pytest.mark.parametrize("outcome", ["failure", "success"])
def test_a_workflow_whose_latest_run_started_no_job_opens_one(hub, outcome):
    hub.end_run("example-flow", 4120, outcome, jobs=0)
    sweep(hub)
    assert len(watched(hub)) == 1
    assert "no job started" in hub.issues[(REPO, watched(hub)[0])].body


def test_the_tests_workflow_non_green_run_opens_nothing_here(hub):
    hub.end_run("example-tests", 4500, "failure", path=TESTS_PATH)
    assert checked_names(hub, REPO) == frozenset({TESTS_PATH})
    assert sweep(hub) == []
    assert watched(hub) == []


def test_six_workflows_with_two_non_green_open_two_issues_and_a_name_no_code_knows_works(hub):
    hub.end_run("example-flow", 4101, "success")
    hub.end_run("other-flow", 4102, "success")
    hub.end_run("moonlight-sync", 4103, "failure")
    hub.end_run("", 4104, "timed_out", path=X_PATH)
    hub.end_run("docs-site", 4105, "skipped", jobs=1)
    hub.end_run("example-tests", 4106, "success", path=TESTS_PATH)
    actions = sweep(hub)
    assert sorted(action.workflow for action in actions) == [".github/workflows/moonlight-sync.yml", X_PATH]
    assert len(watched(hub)) == 2
    assert sorted(watch.workflow_of(hub.issues[(REPO, n)].body) for n in watched(hub)) == [
        ".github/workflows/moonlight-sync.yml", X_PATH]


def test_two_workflows_with_one_display_name_open_one_issue_naming_the_failing_path(hub):
    hub.end_run("example-flow", 4140, "success", path=".github/workflows/a.yml")
    hub.end_run("example-flow", 4141, "failure", path=".github/workflows/b.yml")
    actions = sweep(hub)
    assert [action.workflow for action in actions] == [".github/workflows/b.yml"]
    assert len(watched(hub)) == 1
    assert watch.workflow_of(hub.issues[(REPO, watched(hub)[0])].body) == ".github/workflows/b.yml"


def test_a_failing_tests_yml_and_a_failing_file_of_the_same_display_name_open_one_issue_for_the_other(hub):
    hub.end_run("example-tests", 4150, "failure", path=TESTS_PATH)
    hub.end_run("example-tests", 4151, "failure", path=".github/workflows/other.yml")
    actions = sweep(hub)
    assert [action.workflow for action in actions] == [".github/workflows/other.yml"]
    assert len(watched(hub)) == 1


def test_a_watch_issue_relabelled_ready_for_agent_is_commented_on_not_duplicated(hub):
    hub.end_run("example-flow", 4160, "failure")
    sweep(hub)
    number = watched(hub)[0]
    hub.write(REPO, number, hub.issues[(REPO, number)].body, ["ready-for-agent"])
    hub.end_run("example-flow", 4161, "failure")
    actions = sweep(hub)
    assert [type(action) for action in actions] == [watch.Comment]
    assert actions[0].issue_number == number
    assert len(hub.notes[(REPO, number)]) == 1
    open_numbers = [n for (r, n), issue in hub.issues.items() if r == REPO and issue.open]
    assert open_numbers == [number]


def test_a_non_green_run_on_another_branch_opens_nothing(hub):
    hub.end_run("example-flow", 4170, "failure", branch="feature-x")
    assert sweep(hub) == []
    assert watched(hub) == []


def scripted_gh(asked, *, run_conclusion="failure", jobs=(STARTED, NOT_STARTED), newer=None):
    """A scripted `gh`. `asked` collects the text of every call that reads the runs or the jobs of a workflow.
    With `newer` = (conclusion, jobs), run 4118 is listed above run 4117 with that conclusion and those jobs."""

    def scripted(args):
        text = " ".join(args)
        if args == ["api", f"repos/{REPO}"]:
            return json.dumps({"default_branch": "main", "private": False})
        if "actions/workflows?" in text:
            return json.dumps({"total_count": 2, "workflows": [
                {"id": 1, "name": "example-flow", "path": FLOW_PATH},
                {"id": 2, "name": None, "path": X_PATH}]})
        if "actions/workflows/1/runs?" in text:
            asked.append(text)
            listed = [{"id": 4117, "status": "completed", "conclusion": run_conclusion,
                       "updated_at": "2026-10-28T09:00:00Z", "html_url": URL}]
            if newer is not None:
                listed.insert(0, {"id": 4118, "status": "completed", "conclusion": newer[0],
                                  "updated_at": "2026-10-28T10:00:00Z", "html_url": URL})
            return json.dumps({"total_count": len(listed), "workflow_runs": listed})
        if "actions/workflows/2/runs?" in text:
            return json.dumps({"total_count": 0, "workflow_runs": []})
        if "actions/runs/4118/jobs" in text:
            asked.append(text)
            return json.dumps({"total_count": len(newer[1]), "jobs": list(newer[1])})
        if "actions/runs/4117/jobs" in text:
            asked.append(text)
            return json.dumps({"total_count": len(jobs), "jobs": list(jobs)})
        if "/issues/7/comments" in text:
            return json.dumps([{"user": {"login": "owner", "type": "User"}, "body": watch.run_mark(4120),
                                "created_at": "2026-10-28T10:00:00Z"}])
        if "/issues?state=open" in text:
            return json.dumps([
                {"number": 7, "title": "t", "body": f"{watch.mark(FLOW_PATH)}\n{watch.run_mark(4116)}\n",
                 "labels": [{"name": "ready-for-agent"}], "state": "open"},
                {"number": 8, "title": "t", "body": "no mark", "labels": [{"name": TRIAGE_LABEL}], "state": "open"},
                {"number": 9, "title": "t", "body": f"{watch.mark(FLOW_PATH)}\n", "labels": [],
                 "state": "open", "pull_request": {"url": "https://example.test/pull/9"}}])
        raise AssertionError(f"no answer for {args}")

    return scripted


def scripted_runs(asked, listed):
    """A scripted `gh` whose workflow 1 lists `listed`, newest first, as (run_id, conclusion, jobs, ended); each run's
    jobs are the ones given for it. Workflow 2 has no run. `asked` collects the text of every runs or jobs call."""

    def scripted(args):
        text = " ".join(args)
        if args == ["api", f"repos/{REPO}"]:
            return json.dumps({"default_branch": "main", "private": False})
        if "actions/workflows?" in text:
            return json.dumps({"total_count": 2, "workflows": [
                {"id": 1, "name": "example-flow", "path": FLOW_PATH},
                {"id": 2, "name": None, "path": X_PATH}]})
        if "actions/workflows/1/runs?" in text:
            asked.append(text)
            return json.dumps({"total_count": len(listed), "workflow_runs": [
                {"id": run_id, "status": "completed", "conclusion": conclusion, "updated_at": ended,
                 "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}"}
                for run_id, conclusion, _, ended in listed]})
        if "actions/workflows/2/runs?" in text:
            return json.dumps({"total_count": 0, "workflow_runs": []})
        for run_id, _, jobs, _ in listed:
            if f"actions/runs/{run_id}/jobs" in text:
                asked.append(text)
                return json.dumps({"total_count": len(jobs), "jobs": list(jobs)})
        raise AssertionError(f"no answer for {args}")

    return scripted


def test_the_real_hub_reads_past_a_replaced_newest_run_to_the_success_run_under_it():
    success = {"started_at": "2026-10-28T08:59:00Z", "conclusion": "success"}
    hub = GhPullHub(gh=scripted_runs([], [
        (4119, "cancelled", (), "2026-10-28T10:30:00Z"),
        (4118, "cancelled", (NOT_STARTED,), "2026-10-28T10:00:00Z"),
        (4117, "success", (success,), "2026-10-28T09:00:00Z")]))
    assert hub.latest_workflow_runs(REPO) == [
        watch.WorkflowRun(FLOW_PATH, 4117, f"https://github.com/{REPO}/actions/runs/4117",
                          "2026-10-28T09:00:00Z", "success", 1)]


def test_the_real_hub_leaves_out_a_workflow_whose_ten_newest_runs_were_all_replaced():
    listed = [(4100 + i, "cancelled", (NOT_STARTED,), f"2026-10-28T10:{i:02d}:00Z") for i in range(10)]
    hub = GhPullHub(gh=scripted_runs([], listed))
    latest = hub.latest_workflow_runs(REPO)
    assert latest == []
    assert watch.decide(latest, [], skip=frozenset()) == []


def test_the_real_hub_reads_the_listed_workflows_their_latest_run_and_the_watch_tickets():
    asked = []
    hub = GhPullHub(gh=scripted_gh(asked))
    assert hub.latest_workflow_runs(REPO) == [
        watch.WorkflowRun(FLOW_PATH, 4117, URL, "2026-10-28T09:00:00Z", "failure", 1)]
    assert any("branch=main" in text for text in asked)
    assert hub.workflow_files(REPO) == [FLOW_PATH, X_PATH]
    assert hub.watch_issues(REPO) == [watch.WatchIssue(7, FLOW_PATH, 4120)]


def test_the_real_hub_leaves_out_a_run_whose_jobs_were_all_skipped():
    skipped = {"started_at": "2026-10-28T08:59:00Z", "conclusion": "skipped"}
    hub = GhPullHub(gh=scripted_gh([], run_conclusion="success", jobs=(skipped, skipped)))
    assert hub.latest_workflow_runs(REPO) == []


def test_the_real_hub_reads_past_a_newer_skipped_run_to_the_latest_run_that_is_not_skipped():
    hub = GhPullHub(gh=scripted_gh([], newer=("skipped", ())))
    assert hub.latest_workflow_runs(REPO) == [
        watch.WorkflowRun(FLOW_PATH, 4117, URL, "2026-10-28T09:00:00Z", "failure", 1)]


def test_the_real_hub_asks_no_jobs_of_a_run_already_concluded_skipped():
    asked = []
    hub = GhPullHub(gh=scripted_gh(asked, run_conclusion="skipped", jobs=(SKIPPED_JOB,)))
    assert hub.latest_workflow_runs(REPO) == []
    assert not any("actions/runs/4117/jobs" in text for text in asked)


def test_a_failure_at_start_above_an_older_skipped_run_is_the_latest_and_opens_one_watch():
    asked = []
    hub = GhPullHub(gh=scripted_gh(asked, run_conclusion="skipped", jobs=(SKIPPED_JOB,), newer=("failure", ())))
    latest = hub.latest_workflow_runs(REPO)
    assert latest == [watch.WorkflowRun(FLOW_PATH, 4118, URL, "2026-10-28T10:00:00Z", "failure", 0)]
    actions = watch.decide(latest, [], skip=frozenset())
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    assert actions[0].run.run_id == 4118
