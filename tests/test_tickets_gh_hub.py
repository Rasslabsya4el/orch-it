"""`GhHub` (#398 decisions 4 and 5): the tickets' view of GitHub read and written through
`gh api`, here against a recorded `gh` that answers as GitHub does."""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from orch_it.testruns.github import GithubError
from orch_it.tickets.hub import GhHub

REPO = "owner-x/repo-y"


class Gh:
    """Answers `gh` calls by the first matching (words, answer) pair; records each call and the
    JSON of any `--input` file as it stood when called."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[list[str]] = []
        self.inputs: list[object] = []

    def __call__(self, args):
        self.calls.append(args)
        if "--input" in args:
            self.inputs.append(json.loads(Path(args[args.index("--input") + 1]).read_text("utf-8")))
        for words, answer in self.answers:
            if all(word in args for word in words):
                if isinstance(answer, Exception):
                    raise answer
                return answer if isinstance(answer, str) else json.dumps(answer)
        raise AssertionError(f"no answer for {args}")


NOT_FOUND = GithubError("gh_failed", "api repos/x: gh: Not Found (HTTP 404)")


def test_an_issue_and_a_pull_request_are_read_with_their_state():
    gh = Gh(([f"repos/{REPO}/issues/10"], {
        "number": 10, "title": "T", "body": None, "state": "open", "state_reason": None,
        "labels": [{"name": "parked"}, {"name": "Priority"}]}),
        ([f"repos/{REPO}/issues/12"], {
            "number": 12, "title": "P", "body": "b", "state": "closed", "state_reason": None,
            "labels": [], "pull_request": {"merged_at": "2026-11-02T10:00:00Z"}}),
        ([f"repos/{REPO}/issues/13"], NOT_FOUND))
    hub = GhHub(gh)
    issue = hub.issue(REPO, 10)
    assert (issue.body, issue.labels, issue.open, issue.is_pr) == ("", ("parked", "Priority"), True, False)
    pr = hub.issue(REPO, 12)
    assert (pr.is_pr, pr.merged, pr.open) == (True, True, False)
    assert hub.issue(REPO, 13) is None


def test_the_parked_issues_are_listed_from_every_page_and_from_their_park_lines_without_pull_requests():
    pages = (json.dumps([{"number": 3}, {"number": 4, "pull_request": {}}]) + json.dumps([{"number": 9}]))
    gh = Gh((["--paginate", f"repos/{REPO}/issues?labels=parked&state=open&per_page=100"], pages),
            (["search/issues", f'q=repo:{REPO} is:issue is:open in:body "Parked:"'],
             {"total_count": 2, "items": [{"number": 9}, {"number": 15}]}))
    assert GhHub(gh).parked(REPO) == [3, 9, 15]


def test_body_and_labels_go_in_one_patch():
    gh = Gh((["-X", "PATCH", f"repos/{REPO}/issues/10"], {}))
    GhHub(gh).write(REPO, 10, "Parked: date 2026-11-20 until 2026-11-21\n\nbody", ["parked", "priority"])
    assert len(gh.calls) == 1
    assert gh.inputs == [{"body": "Parked: date 2026-11-20 until 2026-11-21\n\nbody", "labels": ["parked", "priority"]}]


def test_comments_are_read_with_who_wrote_them_and_written_from_a_file():
    gh = Gh(([f"repos/{REPO}/issues/10/comments?per_page=100", "--paginate"], [
        {"user": {"login": "a-person", "type": "User"}, "body": "hi", "created_at": "2026-11-02T10:00:00Z"},
        {"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": "x", "created_at": "2026-11-02T11:00:00Z"},
        {"user": None, "body": "ghost", "created_at": "2026-11-02T12:00:00Z"}]),
        (["-X", "POST", f"repos/{REPO}/issues/10/comments"], {}))
    hub = GhHub(gh)
    said = hub.comments(REPO, 10)
    assert [(c.author, c.bot, c.at) for c in said] == [
        ("a-person", False, datetime(2026, 11, 2, 10, tzinfo=UTC)),
        ("github-actions[bot]", True, datetime(2026, 11, 2, 11, tzinfo=UTC)),
        ("ghost", False, datetime(2026, 11, 2, 12, tzinfo=UTC))]
    hub.comment(REPO, 10, "a long text " * 5000)
    assert gh.inputs[-1] == {"body": "a long text " * 5000}


def test_one_label_comes_off_by_its_url_encoded_name_and_one_already_gone_is_no_error():
    gh = Gh((["-X", "DELETE", f"repos/{REPO}/issues/10/labels/ready%20for%3Aagent"], [{"name": "priority"}]),
            (["-X", "DELETE", f"repos/{REPO}/issues/11/labels/parked"], NOT_FOUND),
            (["-X", "DELETE", f"repos/{REPO}/issues/12/labels/parked"], GithubError("gh_failed", "HTTP 403")))
    hub = GhHub(gh)
    hub.remove_label(REPO, 10, "ready for:agent")
    hub.remove_label(REPO, 11, "parked")
    with pytest.raises(GithubError):
        hub.remove_label(REPO, 12, "parked")


def test_the_last_time_the_parked_label_went_on_is_read_from_the_issue_events():
    gh = Gh(([f"repos/{REPO}/issues/10/events?per_page=100"], [
        {"event": "labeled", "label": {"name": "parked"}, "created_at": "2026-11-01T08:00:00Z"},
        {"event": "unlabeled", "label": {"name": "parked"}, "created_at": "2026-11-01T09:00:00Z"},
        {"event": "labeled", "label": {"name": "Parked"}, "created_at": "2026-11-02T08:00:00Z"},
        {"event": "labeled", "label": {"name": "priority"}, "created_at": "2026-11-03T08:00:00Z"},
        {"event": "closed", "created_at": "2026-11-03T09:00:00Z"}]),
        ([f"repos/{REPO}/issues/11/events?per_page=100"], []))
    hub = GhHub(gh)
    assert hub.parked_at(REPO, 10) == datetime(2026, 11, 2, 8, tzinfo=UTC)
    assert hub.parked_at(REPO, 11) is None


def test_a_run_and_its_end_are_read_and_a_missing_run_is_none():
    gh = Gh(([f"repos/{REPO}/actions/runs/5"], {"status": "completed", "conclusion": "success"}),
            ([f"repos/{REPO}/actions/runs/6"], {"status": "in_progress", "conclusion": None}),
            ([f"repos/{REPO}/actions/runs/7"], {"status": "completed", "conclusion": "failure"}),
            ([f"repos/{REPO}/actions/runs/8"], NOT_FOUND))
    hub = GhHub(gh)
    assert [(s.ended, s.passed) for s in (hub.run(REPO, "5"), hub.run(REPO, "6"), hub.run(REPO, "7"))] == [
        (True, True), (False, False), (True, False)]
    assert hub.run(REPO, "8") is None


def test_the_layer_version_is_the_version_file_else_orch_it_s_own_project_version():
    def content(text):
        return {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}

    gh = Gh(([f"repos/{REPO}/contents/.orch-it/version"], content("0.4.1\n")))
    assert GhHub(gh).layer_version(REPO) == "0.4.1"
    gh = Gh(([f"repos/{REPO}/contents/.orch-it/version"], NOT_FOUND),
            ([f"repos/{REPO}/contents/pyproject.toml"], content('[project]\nname = "orch-it"\nversion = "0.0.3"\n')))
    assert GhHub(gh).layer_version(REPO) == "0.0.3"
    gh = Gh(([f"repos/{REPO}/contents/.orch-it/version"], NOT_FOUND),
            ([f"repos/{REPO}/contents/pyproject.toml"], content('[project]\nname = "other"\nversion = "9.0.0"\n')))
    assert GhHub(gh).layer_version(REPO) is None


def test_the_parent_is_read_from_the_sub_issues():
    gh = Gh((["graphql"], {"data": {"repository": {"issue": {"parent": {
        "number": 355, "repository": {"nameWithOwner": "owner-x/other"}}}}}}))
    assert GhHub(gh).parent(REPO, 10) == ("owner-x/other", 355)
    gh = Gh((["graphql"], {"data": {"repository": {"issue": {"parent": None}}}}))
    assert GhHub(gh).parent(REPO, 10) is None


def test_the_issues_of_a_label_are_listed_from_every_page_without_pull_requests():
    issue = {"title": "T", "body": "", "state": "open", "labels": [{"name": "brain-handoff"}]}
    pages = json.dumps([issue | {"number": 3}, issue | {"number": 4, "pull_request": {}}]) + json.dumps(
        [issue | {"number": 9}])
    gh = Gh((["--paginate", f"repos/{REPO}/issues?labels=brain-handoff&state=open&per_page=100"], pages))
    assert [found.number for found in GhHub(gh).listed(REPO, "brain-handoff")] == [3, 9]


def test_an_issue_is_created_from_a_file_and_pinned_by_its_node_id():
    gh = Gh((["-X", "POST", f"repos/{REPO}/issues"], {"number": 21}),
            ([f"repos/{REPO}/issues/21"], {"number": 21, "node_id": "I_kw21", "labels": []}),
            (["graphql", "id=I_kw21"], {"data": {"pinIssue": {"issue": {"number": 21}}}}),
            (["graphql", "owner=owner-x"], {"data": {"repository": {"pinnedIssues": {"nodes": [
                {"issue": {"number": 21}}, {"issue": None}]}}}}))
    hub = GhHub(gh)
    assert hub.create(REPO, "Brain hand-off", "body", ["brain-handoff"]) == 21
    assert gh.inputs == [{"title": "Brain hand-off", "body": "body", "labels": ["brain-handoff"]}]
    hub.pin(REPO, 21)
    assert any("mutation" in " ".join(call) and "id=I_kw21" in call for call in gh.calls)
    assert hub.pinned(REPO) == [21]


def test_a_layer_label_is_created_only_when_the_repo_has_none_of_its_name():
    gh = Gh((["-X", "POST", f"repos/{REPO}/labels"], {}),
            ([f"repos/{REPO}/labels?per_page=100"], [{"name": "Brain-Handoff", "color": "000000"}]))
    assert GhHub(gh).ensure_label(REPO, "brain-handoff") is False
    gh = Gh((["-X", "POST", f"repos/{REPO}/labels"], {}),
            ([f"repos/{REPO}/labels?per_page=100"], [{"name": "needs-triage", "color": "000000"}]))
    assert GhHub(gh).ensure_label(REPO, "brain-handoff") is True
    assert "name=brain-handoff" in gh.calls[-1]


@pytest.mark.parametrize("answer", ["", "not json", "[1, 2]", '{"number": "x"}'])
def test_an_answer_not_of_the_shape_asked_for_is_bad_output(answer):
    gh = Gh(([f"repos/{REPO}/issues/10"], answer))
    with pytest.raises(GithubError) as failed:
        GhHub(gh).issue(REPO, 10)
    assert failed.value.code == "gh_bad_output"
