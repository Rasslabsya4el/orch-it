"""The update's forge (#397 decision 4): the `gh` calls it makes, read from a stand-in `gh` runner."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from orch_it.merge import marks
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


HEAD = "c" * 40
PULL = "https://github.com/o/r/pull/5"
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class PullGh(FakeGh):
    """Pull request 5 of o/r at `head`: the comments posted to it are read back, `pr merge` is answered."""

    def __init__(self, head, *, fail=()):
        super().__init__(fail=fail)
        self.head, self.posted = head, []

    def __call__(self, args):
        self.calls.append(list(args))
        if tuple(args[:2]) in self.fail:
            raise GithubError("gh_failed", "refused")
        path = next((a for a in args if a.startswith("repos/")), "")
        if args[:3] == ["api", "-X", "POST"]:
            self.posted.append(json.loads(Path(args[args.index("--input") + 1]).read_text("utf-8"))["body"])
            return ""
        if args[:2] == ["api", "--paginate"]:
            if "/issues/" in path and path.endswith("/comments?per_page=100"):
                return json.dumps([{"user": {"login": "o", "type": "User"}, "body": body,
                                    "created_at": "2026-11-02T09:00:00Z"} for body in self.posted])
            return "[]"
        if path == "repos/o/r/pulls/5":
            return json.dumps({"number": 5, "title": "t", "body": "Refs #10", "state": "open", "draft": False,
                               "head": {"sha": self.head, "ref": "ticket-10", "repo": {"full_name": "o/r"}},
                               "base": {"ref": "main"}, "created_at": "2026-11-02T08:00:00Z",
                               "merged_at": None, "mergeable": True})
        if "/compare/" in path:
            return json.dumps({"behind_by": 0})
        return ""


def merges(gh):
    return [c for c in gh.calls if c[:2] == ["pr", "merge"]]


def test_mark_ready_writes_one_ready_mark_at_the_head_and_sets_no_auto_merge():
    gh = PullGh(HEAD)
    GithubForge("o/r", gh=gh).mark_ready(PULL)
    [body] = gh.posted
    assert f"<!-- orch-it ready head={HEAD} -->" in body and "ticket=" not in body
    assert merges(gh) == []  # the merge line merges it, not GitHub


def test_mark_ready_twice_writes_one_mark():
    gh = PullGh(HEAD)
    forge = GithubForge("o/r", gh=gh)
    forge.mark_ready(PULL)
    forge.mark_ready("5")
    assert len(gh.posted) == 1 and merges(gh) == []


def test_a_re_used_pull_request_with_its_mark_at_the_head_keeps_the_mark():
    gh = PullGh(HEAD)
    gh.posted.append(marks.ready_comment(None, HEAD, NOW))
    GithubForge("o/r", gh=gh).mark_ready(PULL)
    assert len(gh.posted) == 1 and merges(gh) == []  # the mark stands, no second one


def test_a_refused_mark_ready_is_forge_failed():
    with pytest.raises(SkillsError) as e:
        GithubForge("o/r", gh=PullGh(HEAD, fail={("api", "-X")})).mark_ready(PULL)
    assert e.value.code == "forge_failed"
