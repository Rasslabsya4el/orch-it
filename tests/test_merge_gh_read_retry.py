"""The merge hub's reads are asked again after a `gh_timeout`; its writes and every other failure are called once.

The seam is the injected `gh` callable, observed through the hub's public methods. Pauses between tries are
recorded on the hub, never slept (`hub.sleep` is set to a list's `append`)."""

import json

import pytest

from orch_it.merge.gh import GhPullHub
from orch_it.testruns.github import GithubError
from orch_it.tickets.hub import GhHub

REPO = "owner-x/repo-y"
ISSUE_PATH = f"repos/{REPO}/issues/5"
ISSUE = json.dumps({"number": 5, "title": "T", "body": "", "labels": [], "state": "open", "node_id": "I_5"})
REVIEWS_PATH = f"repos/{REPO}/pulls/5/reviews?per_page=100"
PULL_COMMENTS_PATH = f"repos/{REPO}/pulls/5/comments?per_page=100"
ISSUE_COMMENTS_PATH = f"repos/{REPO}/issues/5/comments?per_page=100"


class Gh:
    """A recorded `gh`: each call is answered by the first rule whose words are all in it. A rule's outcomes
    are used in order and the last one repeats; an exception outcome is raised, a string is the answer."""

    def __init__(self, *rules):
        self.rules = [(words, list(outcomes)) for words, outcomes in rules]
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(args)
        for words, outcomes in self.rules:
            if all(word in args for word in words):
                outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        raise AssertionError(f"no answer for {args}")

    def times(self, word):
        return sum(1 for call in self.calls if word in call)


def timeout(path):
    return GithubError("gh_timeout", f"api {path}")


def hub_of(cls, gh):
    """The hub over `gh`, its pauses between tries kept in `hub.pauses` instead of slept."""
    hub = cls(gh)
    hub.pauses = []
    hub.sleep = hub.pauses.append
    return hub


# --- reads: a timeout is asked again -----------------------------------------------------------

def test_a_read_that_times_out_once_and_then_answers_returns_the_answer():
    gh = Gh(([ISSUE_PATH], [timeout(ISSUE_PATH), ISSUE]))
    hub = hub_of(GhHub, gh)

    issue = hub.issue(REPO, 5)

    assert issue.number == 5
    assert gh.times(ISSUE_PATH) == 2
    assert len(hub.pauses) == 1


def test_a_read_that_times_out_on_every_try_raises_the_same_timeout_after_one_try_and_its_retries():
    gh = Gh(([ISSUE_PATH], [timeout(ISSUE_PATH)]))
    hub = hub_of(GhHub, gh)

    with pytest.raises(GithubError) as raised:
        hub.issue(REPO, 5)

    assert raised.value.code == "gh_timeout"
    assert gh.times(ISSUE_PATH) == 3  # the first try and the default 2 retries
    assert len(hub.pauses) == 2


# --- reads: other failures are called once -----------------------------------------------------

@pytest.mark.parametrize("code", ["gh_failed", "gh_bad_output"])
def test_a_read_that_fails_with_another_code_is_called_once(code):
    error = GithubError(code, f"api {ISSUE_PATH}")
    gh = Gh(([ISSUE_PATH], [error]))
    hub = hub_of(GhHub, gh)

    with pytest.raises(GithubError) as raised:
        hub.issue(REPO, 5)

    assert raised.value.code == code
    assert gh.times(ISSUE_PATH) == 1
    assert hub.pauses == []


# --- writes: a timeout is called once ----------------------------------------------------------

@pytest.mark.parametrize(("path", "act"), [
    (f"repos/{REPO}/issues/5/comments", lambda hub: hub.comment(REPO, 5, "hi")),
    (f"repos/{REPO}/pulls/5", lambda hub: hub.close_pull(REPO, 5)),
    (f"repos/{REPO}/actions/runs/7/rerun-failed-jobs", lambda hub: hub.rerun(REPO, "7")),
    (f"repos/{REPO}/issues/5/labels/parked", lambda hub: hub.remove_label(REPO, 5, "parked")),
], ids=["post-comment", "patch-pull", "post-rerun", "delete-label"])
def test_a_write_that_times_out_is_called_once(path, act):
    gh = Gh(([path], [timeout(path)]))
    hub = hub_of(GhPullHub, gh)

    with pytest.raises(GithubError) as raised:
        act(hub)

    assert raised.value.code == "gh_timeout"
    assert gh.times(path) == 1
    assert hub.pauses == []


def test_a_graphql_mutation_that_times_out_is_called_once_even_though_its_object_read_is_retried():
    gh = Gh(([ISSUE_PATH], [ISSUE]), (["graphql"], [timeout("graphql")]))
    hub = hub_of(GhHub, gh)

    with pytest.raises(GithubError) as raised:
        hub.pin(REPO, 5)

    assert raised.value.code == "gh_timeout"
    assert gh.times("graphql") == 1


# --- the live case: the reviews page of a pull request times out once --------------------------

def test_pull_comments_whose_reviews_page_times_out_once_still_returns_its_comments():
    said = {"body": "first", "created_at": "2026-11-02T09:00:00Z", "user": {"login": "ann", "type": "User"}}
    review = {"body": "LGTM", "state": "APPROVED", "submitted_at": "2026-11-02T10:00:00Z",
              "user": {"login": "rev", "type": "User"}}
    gh = Gh(
        ([ISSUE_COMMENTS_PATH], [json.dumps([said])]),
        ([REVIEWS_PATH], [timeout(REVIEWS_PATH), json.dumps([review])]),
        ([PULL_COMMENTS_PATH], [json.dumps([])]),
    )
    hub = hub_of(GhPullHub, gh)

    comments = hub.pull_comments(REPO, 5)

    assert [c.body for c in comments] == ["first", "LGTM"]
    assert gh.times(REVIEWS_PATH) == 2
    assert len(hub.pauses) == 1


# --- the spelling of a method -------------------------------------------------------------------

@pytest.mark.parametrize("args, read", [
    (["api", "-X", "GET", "p"], True),
    (["api", "-XGET", "p"], True),
    (["api", "--method=GET", "p"], True),
    (["api", "--method", "GET", "-f", "a=b", "p"], True),
    (["api", "p"], True),
    (["api", "-XPOST", "p"], False),
    (["api", "--method=POST", "p"], False),
    (["api", "--method=DELETE", "p"], False),
    (["api", "-X", "PATCH", "p"], False),
    (["api", "p", "-f", "a=b"], False),
    (["issue", "list"], False),
])
def test_only_a_call_that_names_or_implies_get_is_a_read(args, read):
    from orch_it.tickets.hub import _is_read

    assert _is_read(args) is read


def test_a_write_spelled_with_an_attached_method_that_times_out_is_called_once():
    path = "repos/owner-x/repo-y/issues/5/comments"
    gh = Gh(([path], [timeout(path)]))
    hub = hub_of(GhHub, gh)

    with pytest.raises(GithubError):
        hub._read(["api", "--method=POST", path])

    assert gh.times(path) == 1
