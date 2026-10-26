"""The update's forge (#397 decision 4): the `gh` calls it makes, read from a stand-in `gh` runner."""

import json
from pathlib import Path

import pytest

from orch_it.skills.copies import SkillsError
from orch_it.skills.forge import GithubForge
from orch_it.testruns.github import GithubError


class FakeGh:
    def __init__(self, answers=None, fail=()):
        self.calls, self.answers, self.fail, self.bodies = [], answers or {}, fail, []

    def __call__(self, args):
        self.calls.append(args)
        if "--body-file" in args:
            self.bodies.append(Path(args[args.index("--body-file") + 1]).read_text("utf-8"))
        if tuple(args[:2]) in self.fail:
            raise GithubError("gh_failed", "refused")
        return self.answers.get(tuple(args[:2]), "")


def test_a_missing_label_is_created_as_the_layers_set_has_it():
    gh = FakeGh({("label", "list"): json.dumps([{"name": "bug"}])})
    GithubForge("o/r", gh=gh).add_label("https://github.com/o/r/pull/5", "needs-triage")
    assert gh.calls[1] == ["label", "create", "needs-triage", "--color", "fbca04", "--description",
                           "The brain decides what happens next", "--repo", "o/r"]
    assert gh.calls[2] == ["pr", "edit", "https://github.com/o/r/pull/5", "--add-label", "needs-triage",
                           "--repo", "o/r"]


def test_an_existing_label_is_only_added():
    gh = FakeGh({("label", "list"): json.dumps([{"name": "needs-triage"}])})
    GithubForge("o/r", gh=gh).add_label("5", "needs-triage")
    assert [c[:2] for c in gh.calls] == [["label", "list"], ["pr", "edit"]]


def test_the_update_prs_are_read_from_every_page_and_filtered_by_branch():
    lines = [{"url": "u/1", "branch": "skills-upstream/abc-123", "state": "OPEN"},
             {"url": "u/3", "branch": "skills-upstream/def-456", "state": "MERGED"}]
    gh = FakeGh({("api", "--paginate"): "\n".join(json.dumps(line) for line in lines) + "\n"})
    assert GithubForge("o/r", gh=gh).update_prs() == lines
    [call] = gh.calls
    assert call[:3] == ["api", "--paginate", "repos/o/r/pulls?state=all&per_page=100"]
    assert 'startswith("skills-upstream/")' in call[call.index("--jq") + 1] and "--repo" not in call


def test_the_body_goes_by_file_and_the_rest_are_plain_calls():
    gh = FakeGh({("pr", "create"): "Warning: x\nhttps://github.com/o/r/pull/10\n"})
    forge = GithubForge("o/r", gh=gh)
    assert forge.create_pr("skills-upstream/abc", "t", "a body\nwith | a table |") == "https://github.com/o/r/pull/10"
    assert gh.bodies == ["a body\nwith | a table |"]
    forge.update_branch("u/10")
    forge.close_pr("u/9", "Superseded by u/10")
    assert [c[:3] for c in gh.calls[1:]] == [["pr", "update-branch", "u/10"], ["pr", "close", "u/9"]]
    assert not hasattr(forge, "dispatch_tests") and not hasattr(forge, "merge_when_green")


def test_the_issues_of_any_state_are_read_from_every_page_without_pull_requests():
    lines = [{"url": "i/1", "title": "His skills moved to abc", "labels": ["ready-for-agent"], "state": "OPEN"}]
    gh = FakeGh({("api", "--paginate"): "\n".join(json.dumps(line) for line in lines) + "\n"})
    assert GithubForge("o/r", gh=gh).update_issues() == lines
    [call] = gh.calls
    assert call[:3] == ["api", "--paginate", "repos/o/r/issues?state=all&per_page=100"]
    assert "pull_request" in call[call.index("--jq") + 1]


def test_an_issue_is_opened_edited_and_closed_with_the_layers_labels():
    gh = FakeGh({("label", "list"): json.dumps([{"name": "needs-triage"}]),
                 ("issue", "create"): "https://github.com/o/r/issues/12\n"})
    forge = GithubForge("o/r", gh=gh)
    assert forge.create_issue("His skills moved to abc", "the table", "ready-for-agent") == "https://github.com/o/r/issues/12"
    created = next(c for c in gh.calls if c[:2] == ["issue", "create"])
    assert created[created.index("--label") + 1] == "ready-for-agent" and gh.bodies == ["the table"]
    assert ["label", "create", "ready-for-agent"] == next(c for c in gh.calls if c[:2] == ["label", "create"])[:3]
    forge.edit_issue("i/12", "a new table")
    edited = next(c for c in gh.calls if c[:2] == ["issue", "edit"])
    assert "--add-label" not in edited and "--remove-label" not in edited and gh.bodies[-1] == "a new table"
    forge.close_issue("i/11", "Superseded by i/12")
    assert gh.calls[-1][:5] == ["issue", "close", "i/11", "--comment", "Superseded by i/12"]


def test_a_refusal_is_its_own_error():
    with pytest.raises(SkillsError) as e:
        GithubForge("o/r", gh=FakeGh(fail={("api", "--paginate")})).update_prs()
    assert e.value.code == "forge_failed"
    with pytest.raises(SkillsError) as e:
        GithubForge("o/r", gh=FakeGh(fail={("issue", "close")})).close_issue("i/1", "x")
    assert e.value.code == "forge_failed"


class RacingGh(FakeGh):
    """Another run creates the label between this run's list and its create."""

    def __call__(self, args):
        if args[:2] == ["label", "create"]:
            self.answers[("label", "list")] = json.dumps([{"name": args[2]}])
            self.calls.append(args)
            raise GithubError("gh_failed", "already exists")
        return super().__call__(args)


def test_a_label_another_run_made_at_the_same_moment_is_no_failure():
    gh = RacingGh({("label", "list"): "[]", ("issue", "create"): "https://github.com/o/r/issues/3\n"})
    assert GithubForge("o/r", gh=gh).create_issue("t", "b", "needs-triage") == "https://github.com/o/r/issues/3"
