"""#411 unit 11f: turning auto-merge off on a pull request whose auto-merge is already off is a no-op success.

Live (a throwaway repo of the layer ticket 411 trial): a person's comment started the follow; auto-merge
merged the pull request nine seconds later, while the follow ran; the follow's `gh pr merge --disable-auto`
then got GitHub's `Can't disable auto-merge for this pull request`, which the hub raised. Here the real `GhPullHub.set_auto_merge` runs over the fake GitHub, with a `gh` that refuses as
GitHub does."""

import pytest
from fake_github import PASSED, REPO, FakeGitHub
from follow_world import world

from orch_it.merge import marks
from orch_it.merge.follow import follow
from orch_it.merge.gh import GhPullHub
from orch_it.merge.settings import MergeSettings
from orch_it.testruns.github import GithubError

REFUSAL = "pr merge 6: GraphQL: Can't disable auto-merge for this pull request. (disablePullRequestAutoMerge)"


class GhFake(FakeGitHub):
    """The fake GitHub whose `set_auto_merge` is the product's own, over a `gh` that answers a disable as GitHub does:
    it refuses when the pull request has no auto-merge on or is no longer open."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.race = False  # auto-merge lands the pull request just before the disable reaches GitHub
        self.gh_error: GithubError | None = None

    def set_auto_merge(self, repo, number, method):
        return GhPullHub.set_auto_merge(self, repo, number, method)

    def _auto_merge_is_off(self, repo, number):
        return GhPullHub._auto_merge_is_off(self, repo, number)

    def gh(self, args):
        assert args[:2] == ["pr", "merge"] and "--disable-auto" in args, args
        number = int(args[2])
        if self.gh_error is not None:
            raise self.gh_error
        if self.race:
            self.checks_on(self.head(number), PASSED)
            assert FakeGitHub.merge(self, REPO, number, self.head(number), "merge")
        found = self.pulls[number]
        if found.state != "open" or found.auto_merge is None:
            raise GithubError("gh_failed", REFUSAL)
        self.calls.append(("auto_merge", number, None))
        found.auto_merge = None
        return ""

    def pull(self, repo, number):
        return FakeGitHub.pull(self, repo, number)


def hub_world(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.__class__ = GhFake
    hub.race = False
    hub.gh_error = None
    return hub, store, prs[10]


def comment_and_follow(hub, store, pr):
    hub.later(minutes=1)
    hub.note(pr, "Wait, please look at the zones before this lands.", author="owner-x")
    return follow(hub, store, REPO, pr, base="main", settings=MergeSettings(), now=hub.now)


def test_a_comment_on_a_pull_request_whose_auto_merge_is_off_ends_as_the_comment_end_expects(tmp_path):
    hub, store, pr = hub_world(tmp_path)
    hub.pulls[pr].auto_merge = None
    done = comment_and_follow(hub, store, pr)
    assert done.outcome == "commented"
    pull = hub.pull(REPO, pr)
    assert pull.auto_merge is None and pull.state == "open"
    assert marks.ready_since(hub.comments(REPO, pr)) is None
    issue = hub.issues[(REPO, 10)]
    assert "needs-triage" in issue.labels and "ready-for-agent" not in issue.labels


def test_a_pull_request_merged_by_auto_merge_while_the_follow_ran_is_no_failure(tmp_path):
    hub, store, pr = hub_world(tmp_path)
    hub.race = True
    done = comment_and_follow(hub, store, pr)
    assert done.outcome == "commented"
    pull = hub.pull(REPO, pr)
    assert pull.state == "merged" and pull.auto_merge is None
    assert "needs-triage" in hub.issues[(REPO, 10)].labels


def test_a_refusal_while_auto_merge_is_still_on_raises(tmp_path):
    hub, _, pr = hub_world(tmp_path)
    hub.pulls[pr].auto_merge = "merge"
    hub.gh_error = GithubError("gh_failed", REFUSAL)
    with pytest.raises(GithubError):
        hub.set_auto_merge(REPO, pr, None)
    assert hub.pull(REPO, pr).auto_merge == "merge"


def test_a_different_gh_failure_still_raises(tmp_path):
    hub, _, pr = hub_world(tmp_path)
    hub.pulls[pr].auto_merge = None
    hub.gh_error = GithubError("gh_failed", "pr merge 6: HTTP 502: Bad Gateway")
    with pytest.raises(GithubError, match="502"):
        hub.set_auto_merge(REPO, pr, None)
