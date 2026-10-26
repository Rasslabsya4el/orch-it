"""The update's forge (#397 decision 4): the `gh` calls it makes, read from a stand-in for `gh`."""

import json
import subprocess

import pytest

from orch_it.skills import forge
from orch_it.skills.copies import SkillsError
from orch_it.skills.forge import GithubForge


class FakeGh:
    def __init__(self, answers):
        self.calls, self.answers = [], answers

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        for prefix, (code, out) in self.answers.items():
            if argv[1:1 + len(prefix)] == list(prefix):
                return subprocess.CompletedProcess(argv, code, out, "refused" if code else "")
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_a_missing_label_is_created_as_the_layers_set_has_it(monkeypatch):
    gh = FakeGh({("label", "list"): (0, json.dumps([{"name": "bug"}]))})
    monkeypatch.setattr(forge.subprocess, "run", gh)
    GithubForge("o/r").add_label("https://github.com/o/r/pull/5", "needs-triage")
    assert gh.calls[1] == ["gh", "label", "create", "needs-triage", "--color", "fbca04", "--description",
                           "The brain decides what happens next", "--repo", "o/r"]
    assert gh.calls[2] == ["gh", "pr", "edit", "https://github.com/o/r/pull/5", "--add-label", "needs-triage",
                           "--repo", "o/r"]


def test_an_existing_label_is_only_added(monkeypatch):
    gh = FakeGh({("label", "list"): (0, json.dumps([{"name": "needs-triage"}]))})
    monkeypatch.setattr(forge.subprocess, "run", gh)
    GithubForge("o/r").add_label("5", "needs-triage")
    assert [c[1:3] for c in gh.calls] == [["label", "list"], ["pr", "edit"]]


def test_the_open_pr_of_a_branch_and_the_calls_of_the_rest(monkeypatch):
    gh = FakeGh({("pr", "list"): (0, json.dumps([{"url": "https://github.com/o/r/pull/9"}])),
                 ("pr", "create"): (0, "Warning: x\nhttps://github.com/o/r/pull/10\n")})
    monkeypatch.setattr(forge.subprocess, "run", gh)
    f = GithubForge("o/r")
    assert f.open_pr("skills-update/abc") == "https://github.com/o/r/pull/9"
    assert f.create_pr("skills-update/abc", "t", "body") == "https://github.com/o/r/pull/10"
    f.merge_when_green("https://github.com/o/r/pull/10")
    f.dispatch_tests("skills-update/abc")
    assert gh.calls[-2][1:5] == ["pr", "merge", "https://github.com/o/r/pull/10", "--auto"]
    assert gh.calls[-1][1:7] == ["workflow", "run", "tests.yml", "--ref", "skills-update/abc", "-f"]


def test_a_refusal_is_its_own_error(monkeypatch):
    monkeypatch.setattr(forge.subprocess, "run", FakeGh({("pr", "list"): (1, "")}))
    with pytest.raises(SkillsError) as e:
        GithubForge("o/r").open_pr("b")
    assert e.value.code == "forge_failed"
