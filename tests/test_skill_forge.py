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


def test_the_update_prs_are_the_skills_update_branches_of_any_state():
    gh = FakeGh({("pr", "list"): json.dumps([
        {"url": "u/1", "headRefName": "skills-update/abc-123", "state": "OPEN"},
        {"url": "u/2", "headRefName": "feature", "state": "OPEN"},
        {"url": "u/3", "headRefName": "skills-update/def-456", "state": "CLOSED"}])})
    assert GithubForge("o/r", gh=gh).update_prs() == [
        {"url": "u/1", "branch": "skills-update/abc-123", "state": "OPEN"},
        {"url": "u/3", "branch": "skills-update/def-456", "state": "CLOSED"}]
    assert gh.calls[0][:4] == ["pr", "list", "--state", "all"]


def test_the_body_goes_by_file_and_the_rest_are_plain_calls():
    gh = FakeGh({("pr", "create"): "Warning: x\nhttps://github.com/o/r/pull/10\n"})
    forge = GithubForge("o/r", gh=gh)
    assert forge.create_pr("skills-update/abc", "t", "a body\nwith | a table |") == "https://github.com/o/r/pull/10"
    assert gh.bodies == ["a body\nwith | a table |"]
    forge.merge_when_green("u/10")
    forge.update_branch("u/10")
    forge.close_pr("u/9", "Superseded by u/10")
    forge.dispatch_tests("skills-update/abc")
    assert [c[:3] for c in gh.calls[1:]] == [["pr", "merge", "u/10"], ["pr", "update-branch", "u/10"],
                                            ["pr", "close", "u/9"], ["workflow", "run", "tests.yml"]]
    assert gh.calls[-1][3:7] == ["--ref", "skills-update/abc", "-f", "slow_and_live=false"]


def test_a_refusal_is_its_own_error():
    with pytest.raises(SkillsError) as e:
        GithubForge("o/r", gh=FakeGh(fail={("pr", "list")})).update_prs()
    assert e.value.code == "forge_failed"
