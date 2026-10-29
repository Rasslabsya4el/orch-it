"""An in-memory GitHub for the ticket commands (#398): issues with bodies and labels, their
comments and `parked` label times, sub-issue parents, Actions runs and layer versions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from orch_it.testruns.github import GithubError
from orch_it.tickets.hub import Comment, Issue, RunState

REPO = "owner-x/repo-y"


class FakeHub:
    WRITES = ("write", "remove_label", "comment", "create", "pin", "label_created")
    budget_left = None  # (remaining, limit) a test sets to forge the request budget GitHub reports; None: unread

    def rate_limit(self):
        return self.budget_left

    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
        self.issues: dict[tuple[str, int], Issue] = {}
        self.notes: dict[tuple[str, int], list[Comment]] = {}
        self.label_times: dict[tuple[str, int], datetime] = {}
        self.parents: dict[tuple[str, int], tuple[str, int]] = {}
        self.runs: dict[tuple[str, str], RunState] = {}
        self.versions: dict[str, str] = {}
        self.writes: list[tuple[str, int, str, list[str]]] = []
        self.failing: set[tuple[str, int]] = set()  # issues every call about fails
        self.pins: dict[str, list[int]] = {}
        self.repo_labels: dict[str, set[str]] = {}  # a repo not named here holds the layer's whole set
        self.calls: list[tuple] = []  # the writes, in order: (name, repo, ...)

    # building the world

    def add(self, number: int, body: str = "", labels=(), *, repo: str = REPO, title: str = "A ticket",
            open: bool = True, state_reason=None, is_pr: bool = False, merged: bool = False) -> Issue:
        issue = Issue(repo, number, title, body, tuple(labels), open, state_reason, is_pr, merged)
        self.issues[(repo, number)] = issue
        if "parked" in labels:
            self.label_times[(repo, number)] = self.now
        return issue

    def close(self, number: int, reason: str = "completed", *, repo: str = REPO, merged: bool = False):
        self.issues[(repo, number)] = replace(self.issues[(repo, number)], open=False, state_reason=reason,
                                              merged=merged)

    def say(self, number: int, body: str, *, author: str = "someone", bot: bool = False, repo: str = REPO,
            at: datetime | None = None):
        self.notes.setdefault((repo, number), []).append(Comment(author, bot, body, at or self.now))

    def later(self, **delta):
        self.now += timedelta(**delta)

    # the Hub

    def _check(self, repo, number):
        if (repo, number) in self.failing:
            raise GithubError("gh_failed", f"{repo}#{number}: HTTP 502")

    def issue(self, repo, number):
        self._check(repo, number)
        return self.issues.get((repo, number))

    def parent(self, repo, number):
        self._check(repo, number)
        return self.parents.get((repo, number))

    def parked(self, repo):
        return sorted(n for (r, n), issue in self.issues.items()
                      if r == repo and issue.open and not issue.is_pr
                      and ("parked" in issue.labels or "Parked:" in issue.body))

    def write(self, repo, number, body, labels):
        self._check(repo, number)
        before = self.issues[(repo, number)]
        if "parked" in labels and "parked" not in before.labels:
            self.label_times[(repo, number)] = self.now
        self.issues[(repo, number)] = replace(before, body=body, labels=tuple(labels))
        self.writes.append((repo, number, body, list(labels)))
        self.calls.append(("write", repo, number))

    def remove_label(self, repo, number, name):
        self._check(repo, number)
        before = self.issues[(repo, number)]
        self.issues[(repo, number)] = replace(before, labels=tuple(l for l in before.labels if l != name))

    def comment(self, repo, number, body):
        self._check(repo, number)
        self.say(number, body, author="the-layer", repo=repo)
        self.calls.append(("comment", repo, number))

    def comments(self, repo, number):
        self._check(repo, number)
        return list(self.notes.get((repo, number), []))

    def parked_at(self, repo, number):
        self._check(repo, number)
        return self.label_times.get((repo, number))

    def run(self, repo, run_id):
        return self.runs.get((repo, run_id))

    def layer_version(self, repo):
        return self.versions.get(repo)

    def listed(self, repo, label):
        return [issue for (r, _), issue in sorted(self.issues.items())
                if r == repo and issue.open and not issue.is_pr and label in issue.labels]

    def open_issues(self, repo):
        return [issue for (r, _), issue in sorted(self.issues.items()) if r == repo and issue.open and not issue.is_pr]

    def create(self, repo, title, body, labels):
        number = max([n for (r, n) in self.issues if r == repo] or [0]) + 1
        self.add(number, body, labels, repo=repo, title=title)
        self.calls.append(("create", repo, number))
        return number

    def pinned(self, repo):
        return list(self.pins.get(repo, []))

    def pin(self, repo, number):
        self._check(repo, number)
        if len(self.pins.get(repo, [])) >= 3:
            raise GithubError("gh_failed", "pinIssue: a repository can pin at most 3 issues")
        self.pins.setdefault(repo, []).append(number)
        self.calls.append(("pin", repo, number))

    def ensure_label(self, repo, name):
        from orch_it.labels import label_set

        held = self.repo_labels.setdefault(repo, set(label_set()))
        if name in held:
            return False
        held.add(name)
        self.calls.append(("label_created", repo, name))
        return True
