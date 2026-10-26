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
    forge.merge_when_green("u/10")
    forge.update_branch("u/10")
    forge.close_pr("u/9", "Superseded by u/10")
    forge.dispatch_tests("skills-upstream/abc")
    assert [c[:3] for c in gh.calls[1:]] == [["pr", "merge", "u/10"], ["pr", "update-branch", "u/10"],
                                            ["pr", "close", "u/9"], ["workflow", "run", "tests.yml"]]
    assert gh.calls[-1][3:7] == ["--ref", "skills-upstream/abc", "-f", "slow_and_live=false"]


def test_a_refusal_is_its_own_error():
    with pytest.raises(SkillsError) as e:
        GithubForge("o/r", gh=FakeGh(fail={("api", "--paginate")})).update_prs()
    assert e.value.code == "forge_failed"
    with pytest.raises(SkillsError) as e:
        GithubForge("o/r", gh=FakeGh(fail={("pr", "merge")})).merge_when_green("u/1")
    assert e.value.code == "forge_failed"
