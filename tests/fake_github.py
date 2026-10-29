"""An in-memory GitHub for the merge flow (#400): the tickets' `FakeHub` plus pull requests with
their heads, bases and auto-merge, check runs per sha, Actions runs with their failed tests, the
runs of the `tests` workflow on a branch, and branch protection's strict rule: a merge needs the
head up to date with its base and every required check green on the head.

A branch is a list of commit shas; a pull request's head is its branch's last sha, and it is
`behind` by the base's shas its branch does not hold. `land` adds a commit to a base (another
pull request merged, a push), `push` adds one to a branch. A sha's checks are set with `checks_on`.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from fake_hub import REPO, FakeHub

from orch_it.merge import workflow_watch as watch
from orch_it.merge.hub import FAILED, NO_RESULT, PASSED, PENDING, Check, MainRun, Pull
from orch_it.testruns.github import GithubError
from orch_it.tickets.hub import Comment

REQUIRED = ("linux", "windows")
DEFAULT_BRANCH = "main"


@dataclass
class FakeFlow:
    """A workflow GitHub lists: its name ("" when it has none), its file, its completed runs newest first, each
    with the branch it ran on."""

    name: str
    path: str
    runs: list = field(default_factory=list)  # (WorkflowRun, branch) pairs


@dataclass
class FakePull:
    number: int
    title: str
    body: str
    branch: str
    base: str
    created_at: datetime
    draft: bool = False
    auto_merge: str | None = None
    state: str = "open"
    conflict: tuple[str, ...] = ()  # the files that conflict with the base, when not empty
    merged_at: datetime | None = None
    merge_sha: str | None = None
    closed_at: datetime | None = None
    head_repo: str = REPO


@dataclass
class FakeRun:
    run_id: str
    sha: str
    attempt: int = 1
    failing: list[str] | None = field(default_factory=list)  # None: no report to read


class FakeGitHub(FakeHub):
    def __init__(self, now=None):
        super().__init__(now)
        self.branches: dict[str, list[str]] = {"main": ["m0"]}
        self.pulls: dict[int, FakePull] = {}
        self.check_runs: dict[str, dict[str, Check]] = {}
        self.runs_by_id: dict[str, FakeRun] = {}
        self.main_runs: list[MainRun] = []
        self.commit_pulls: dict[str, list[int]] = {}
        self.actions = True
        self.calls: list[tuple] = []  # every write, in order
        self.on_merge = []  # callables run after each merge, given the pull request number
        self.merge_refusals = 0  # the next merges GitHub refuses for a reason of its own
        self.next_number = 100
        self.event_files: dict[str, dict[str, str]] = {}  # artifact name -> {relative path: text}
        self.flows: list[FakeFlow] = []  # the workflows GitHub lists, in the order they were listed
        self.default_branch = DEFAULT_BRANCH
        self._shas = 0

    # building the world

    def sha(self, prefix="c"):
        self._shas += 1
        return f"{prefix}{self._shas}"

    def open_pull(self, number=None, *, branch=None, base="main", body="", title="A change", behind=0,
                  draft=False, auto_merge=None, conflict=()):
        number = number or self._number()
        branch = branch or f"branch-{number}"
        held = self.branches[base][:len(self.branches[base]) - behind]
        self.branches[branch] = [*held, self.sha(f"h{number}-")]
        self.pulls[number] = FakePull(number, title, body, branch, base, self.now, draft, auto_merge,
                                      conflict=tuple(conflict))
        self.add(number, body, (), title=title, is_pr=True)
        return number

    def head(self, number):
        return self.branches[self.pulls[number].branch][-1]

    def land(self, base="main", number=None):
        """A commit on `base` from elsewhere: another pull request merged, or a push."""

        sha = self.sha("m")
        self.branches[base].append(sha)
        if number is not None:
            self.commit_pulls[sha] = [number]
        return sha

    def push(self, number):
        sha = self.sha(f"h{number}-")
        self.branches[self.pulls[number].branch].append(sha)
        return sha

    def checks_on(self, sha, state=PASSED, *, names=REQUIRED, run_id=None, attempt=1, failing=(), started=None):
        run_id = run_id or f"run-{sha}-{attempt}"
        self.runs_by_id[run_id] = FakeRun(run_id, sha, attempt, None if failing is None else list(failing))
        for name in names:
            self.check_runs.setdefault(sha, {})[name] = Check(name, state, run_id, attempt, started or self.now)
        return run_id

    def main_run(self, sha, jobs, *, event="push", attempt=1, run_id=None, failing=()):
        run_id = run_id or f"main-{sha}-{attempt}"
        self.runs_by_id[run_id] = FakeRun(run_id, sha, attempt, None if failing is None else list(failing))
        self.main_runs.insert(0, MainRun(run_id, sha, event, attempt, self.now, dict(jobs)))
        return run_id

    def note(self, number, body, *, author="someone", bot=False, at=None):
        self.say(number, body, author=author, bot=bot, at=at)

    def _number(self):
        self.next_number += 1
        while (REPO, self.next_number) in self.issues:
            self.next_number += 1
        return self.next_number

    # the PullHub

    def pull(self, repo, number):
        self._check(repo, number)
        found = self.pulls.get(number)
        if found is None:
            return None
        branch, base = self.branches[found.branch], self.branches[found.base]
        behind = len([sha for sha in base if sha not in branch])
        return Pull(repo, number, found.title, found.body, found.branch, branch[-1], found.base, found.state,
                    found.draft, found.auto_merge, "conflicting" if found.conflict else "mergeable", behind,
                    found.created_at, found.merged_at, found.merge_sha, found.closed_at, found.head_repo)

    def open_pulls(self, repo, base):
        return [self.pull(repo, n) for n, p in sorted(self.pulls.items(), key=lambda item: item[1].created_at)
                if p.state == "open" and p.base == base]

    def checks(self, repo, sha):
        return list(self.check_runs.get(sha, {}).values())

    def pull_comments(self, repo, number):
        self._check(repo, number)
        return list(self.notes_of(number))

    def notes_of(self, number):
        return self.comments(REPO, number)

    def comment(self, repo, number, body):
        # the layer writes under the owner's `gh`: its comments carry the owner's login
        self._check(repo, number)
        self.say(number, body, author=repo.split("/")[0], repo=repo)

    def create_pull(self, repo, base, branch, title, body):
        self.calls.append(("create_pull", branch))
        number = self._number()
        self.pulls[number] = FakePull(number, title, body, branch, base, self.now)
        self.add(number, body, (), title=title, is_pr=True)
        return number

    def set_auto_merge(self, repo, number, method):
        self.calls.append(("auto_merge", number, method))
        self.pulls[number].auto_merge = method

    def update_branch(self, repo, number, head_sha):
        self.calls.append(("update_branch", number))
        found = self.pulls[number]
        if self.head(number) != head_sha:
            raise GithubError("gh_failed", "expected head sha did not match (HTTP 422)")
        if found.conflict:
            return False
        branch = self.branches[found.branch]
        branch += [sha for sha in self.branches[found.base] if sha not in branch]
        branch.append(self.sha(f"u{number}-"))
        return True

    def merge(self, repo, number, head_sha, method):
        self.calls.append(("merge", number, method))
        found, pull = self.pulls[number], self.pull(repo, number)
        if self.merge_refusals:
            self.merge_refusals -= 1
            return None
        green = {c.name: c.state for c in self.checks(repo, pull.head_sha)}
        if (pull.head_sha != head_sha or pull.behind or pull.state != "open" or found.conflict
                or any(green.get(name) != PASSED for name in REQUIRED)):
            return None
        sha = self.land(found.base, number)
        found.state, found.merged_at, found.merge_sha, found.auto_merge = "merged", self.now, sha, None
        self.close(number, merged=True)
        for hook in list(self.on_merge):
            hook(number)
        return sha

    def close_pull(self, repo_or_number, number=None):
        number = repo_or_number if number is None else number
        self.calls.append(("close_pull", number))
        found = self.pulls[number]
        found.state, found.closed_at, found.auto_merge = "closed", self.now, None
        self.close(number, reason=None)

    def rerun(self, repo, run_id):
        self.calls.append(("rerun", run_id))
        run = self.runs_by_id[run_id]
        for sha, checks in self.check_runs.items():
            for name, check in list(checks.items()):
                if check.run_id == run_id:
                    checks[name] = replace(check, state=PENDING, attempt=check.attempt + 1)
        run.attempt += 1

    def cancel(self, repo, run_id):
        self.calls.append(("cancel", run_id))
        for checks in self.check_runs.values():
            for name, check in list(checks.items()):
                if check.run_id == run_id and check.state == PENDING:
                    checks[name] = replace(check, state=NO_RESULT)

    def conflict_files(self, repo, base, number):
        return sorted(self.pulls[number].conflict)

    def failing_ids(self, repo, run_id):
        run = self.runs_by_id.get(run_id)
        return None if run is None or run.failing is None else sorted(run.failing)

    def branch_runs(self, repo, branch):
        return list(self.main_runs)

    def pulls_of_commit(self, repo, sha):
        return list(self.commit_pulls.get(sha, []))

    def issues_with_label(self, repo, label):
        return sorted(n for (r, n), issue in self.issues.items()
                      if r == repo and issue.open and not issue.is_pr and label in issue.labels)

    def create_issue(self, repo, title, body, labels):
        number = self._number()
        self.calls.append(("create_issue", number, title))
        self.add(number, body, labels, repo=repo, title=title)
        return number

    def close_issue(self, repo, number, reason):
        self.calls.append(("close_issue", number, reason))
        self.close(number, reason, repo=repo)

    def actions_enabled(self, repo):
        return self.actions

    def event_artifacts(self, repo):
        return [("0", name) for name in sorted(self.event_files)]

    def download_artifact(self, repo, run_id, name, folder):
        for relative, text in self.event_files[name].items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, "utf-8")

    def later(self, **delta):
        super().later(**delta)

    # the workflow watch

    def end_run(self, name, run_id, outcome="success", *, jobs=3, path=None, branch=DEFAULT_BRANCH):
        """A completed run of a workflow, the newest of its runs; a workflow new to the listing is added to it.
        `name` is the display name ("" for a workflow with no name); the run carries its file `path`, which is
        the workflow's identity, and the `branch` it ran on."""

        path = path or f".github/workflows/{name}.yml"
        flow = next((f for f in self.flows if f.path == path), None)
        if flow is None:
            flow = FakeFlow(name, path)
            self.flows.append(flow)
        url = f"https://github.com/{REPO}/actions/runs/{run_id}"
        run = watch.WorkflowRun(path, run_id, url, self.now.isoformat(), outcome, jobs)
        flow.runs.insert(0, (run, branch))

    def latest_workflow_runs(self, repo):
        """The latest completed run of each workflow on the default branch, as GitHub lists them."""
        found = []
        for flow in self.flows:
            on_default = [run for run, branch in flow.runs if branch == self.default_branch]
            if on_default:
                found.append(on_default[0])
        return found

    def workflow_files(self, repo):
        return [flow.path for flow in self.flows]

    def watch_issues(self, repo):
        found = []
        for (r, number), issue in sorted(self.issues.items(), key=lambda item: item[0]):
            if r != repo or not issue.open or issue.is_pr:
                continue
            workflow = watch.workflow_of(issue.body)
            if workflow is None:
                continue
            texts = [issue.body, *(c.body for c in self.notes.get((repo, number), []))]
            found.append(watch.WatchIssue(number, workflow, watch.last_run_id(texts)))
        return found


def ago(hub, **delta):
    return hub.now - timedelta(**delta)


__all__ = ["FAILED", "NO_RESULT", "PASSED", "PENDING", "REPO", "REQUIRED", "Comment", "FakeGitHub", "ago"]
