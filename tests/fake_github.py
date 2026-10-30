"""An in-memory GitHub for the merge flow (#400): the tickets' `FakeHub` plus pull requests with
their heads and bases, check runs per sha, Actions runs with their failed tests, the
runs of the `tests` workflow on a branch, and branch protection's strict rule: a merge needs the
head up to date with its base and every required check green on the head.

A branch is a list of commit shas; a pull request's head is its branch's last sha, and it is
`behind` by the base's shas its branch does not hold. `land` adds a commit to a base (another
pull request merged, a push), `push` adds one to a branch. A sha's checks are set with `checks_on`.
"""

import json
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from fake_hub import REPO, FakeHub

from orch_it.merge import workflow_watch as watch
from orch_it.merge.hub import FAILED, NO_RESULT, PASSED, PENDING, Check, LandingRefused, MainRun, Pull, Result
from orch_it.testruns.config import DEFAULT_REQUIRED_CHECKS
from orch_it.testruns.github import GithubError
from orch_it.tickets.hub import Comment

REQUIRED = DEFAULT_REQUIRED_CHECKS
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
    passed: list[str] = field(default_factory=list)  # the ids every job's report holds passed
    jobs: dict[str, str] = field(default_factory=dict)  # job -> its state; the jobs whose reports the run holds
    reports: list[Result] | None = None  # the run's reports as given; else built from `failing` and `passed`

    def results(self) -> list[Result] | None:
        """The run's reports: `reports` when given; else each of `passed` in every job and each of `failing` in the
        jobs that failed (every job when none did); None when the run has no report to read."""

        if self.failing is None:
            return None
        if self.reports is not None:
            return list(self.reports)
        jobs = self.jobs or {name: PASSED for name in REQUIRED}
        red = [job for job, state in jobs.items() if state == FAILED] or list(jobs)
        return ([Result(job, test_id, "passed", False) for job in jobs for test_id in self.passed]
                + [Result(job, test_id, "failed", False) for job in red for test_id in self.failing])


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
        self.main_branch = DEFAULT_BRANCH  # the repo's default branch, as `default_branch` reads it
        self.required_by_base: dict[str, set[str]] = {}  # base -> the check names its protection requires
        self.required = REQUIRED  # the check names a merge is held to (a test may name its own)
        self.clashes: set[frozenset[int]] = set()  # pairs of pull requests that conflict when combined
        self.advances: list[str] = []  # every sha a base was moved forward to, in order
        self.batch_members: dict[str, list[int]] = {}  # batch branch -> the pull requests merged into it, in order
        self.trees: dict[str, list[str]] = {}  # a batch commit's sha -> the commits it holds
        self.merge_lag_seconds = 0  # seconds GitHub takes to mark a pull request merged after a base advance
        self.lagging: dict[int, tuple[datetime, str]] = {}  # pr -> (when it shows merged, the sha it merged at)
        self.advance_refusal: str | None = None  # the message of a 422 that `advance_base` raises (protection)
        self._shas = 0

    # building the world

    def sha(self, prefix="c"):
        self._shas += 1
        return f"{prefix}{self._shas}"

    def open_pull(self, number=None, *, branch=None, base="main", body="", title="A change", behind=0,
                  draft=False, conflict=()):
        number = number or self._number()
        branch = branch or f"branch-{number}"
        held = self.branches[base][:len(self.branches[base]) - behind]
        self.branches[branch] = [*held, self.sha(f"h{number}-")]
        self.pulls[number] = FakePull(number, title, body, branch, base, self.now, draft,
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

    def main_run(self, sha, jobs, *, event="push", attempt=1, run_id=None, failing=(), passed=(), results=None):
        """A run of the `tests` workflow on a branch. `passed`: ids every job's report holds passed; `results`: the
        reports as `Result`s, which then name the failing ids too."""

        run_id = run_id or f"main-{sha}-{attempt}"
        if results is not None:
            failing = sorted({r.test_id for r in results if r.outcome in ("failed", "error")})
        self.runs_by_id[run_id] = FakeRun(run_id, sha, attempt, None if failing is None else list(failing),
                                          list(passed), dict(jobs), None if results is None else list(results))
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
        self._mark_due()
        found = self.pulls.get(number)
        if found is None:
            return None
        branch, base = self.branches[found.branch], self.branches[found.base]
        behind = len([sha for sha in base if sha not in branch])
        return Pull(repo, number, found.title, found.body, found.branch, branch[-1], found.base, found.state,
                    found.draft, "conflicting" if found.conflict else "mergeable", behind,
                    found.created_at, found.merged_at, found.merge_sha, found.closed_at, found.head_repo)

    def open_pulls(self, repo, base):
        return [self.pull(repo, n) for n, p in sorted(self.pulls.items(), key=lambda item: item[1].created_at)
                if p.state == "open" and p.base == base]

    def checks(self, repo, sha, *, statuses=False):
        return list(self.check_runs.get(sha, {}).values())

    def post_status(self, repo, sha, name, state, description):
        self.calls.append(("post_status", sha, name, state))
        self.checks_on(sha, state, names=(name,))

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

    def required_checks_of(self, repo, base):
        return set(self.required_by_base.get(base, set()))

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
                or any(green.get(name) != PASSED for name in self.required)):
            return None
        sha = self.land(found.base, number)
        found.state, found.merged_at, found.merge_sha = "merged", self.now, sha
        self.close(number, merged=True)
        for hook in list(self.on_merge):
            hook(number)
        return sha

    def start_batch(self, repo, base, branch):
        self.calls.append(("start_batch", branch))
        self.branches[branch] = list(self.branches[base])
        self.batch_members[branch] = []
        return self.branches[branch][-1]

    def add_to_batch(self, repo, branch, head_sha):
        self.calls.append(("add_to_batch", branch, head_sha))
        number = next(n for n in self.pulls if self.head(n) == head_sha)
        found, held = self.pulls[number], self.batch_members[branch]
        if found.conflict or any(frozenset((number, other)) in self.clashes for other in held):
            return None
        tree = self.branches[branch]
        tree += [sha for sha in self.branches[found.branch] if sha not in tree]
        tree.append(self.sha("b"))
        held.append(number)
        self.trees[tree[-1]] = list(tree)
        return tree[-1]

    def advance_base(self, repo, base, sha):
        self.calls.append(("advance_base", base, sha))
        if self.advance_refusal is not None:
            raise LandingRefused(self.advance_refusal)
        branch = next((b for b, tree in self.branches.items() if b in self.batch_members and tree[-1] == sha), None)
        main = self.branches[base]
        if branch is None or self.branches[branch][:len(main)] != main:
            return False
        held = {name for name, check in self.check_runs.get(sha, {}).items() if check.state == PASSED}
        if self.required_by_base.get(base, set()) - held:  # protection with admins included: a name not green refuses
            raise LandingRefused("Required status checks are expected: "
                                 + ", ".join(sorted(self.required_by_base[base] - held)))
        main[:] = self.branches[branch]
        self.advances.append(sha)
        for number in self.batch_members[branch]:
            self.lagging[number] = (self.now + timedelta(seconds=self.merge_lag_seconds), sha)
        self._mark_due()
        return True

    def _mark_due(self):
        """The pull requests of an advanced base that GitHub has marked merged by now."""

        for number, (due, sha) in sorted(self.lagging.items()):
            if due <= self.now:
                del self.lagging[number]
                self._mark_merged(number, sha)

    def _mark_merged(self, number, sha):
        found = self.pulls[number]
        found.state, found.merged_at, found.merge_sha = "merged", self.now, sha
        self.commit_pulls[sha] = [*self.commit_pulls.get(sha, []), number]
        self.close(number, merged=True)
        for hook in list(self.on_merge):
            hook(number)

    def end_batch(self, repo, branch):
        self.calls.append(("end_batch", branch))
        self.branches.pop(branch, None)
        self.batch_members.pop(branch, None)

    def batch_branches(self, repo):
        return sorted(b for b in self.branches if b.startswith("orch-batch/"))

    def members_of(self, sha):
        """The pull requests whose heads the commit `sha` holds, ascending."""

        tree = self.trees.get(sha, [])
        return sorted(n for n in self.pulls if self.head(n) in tree)

    def close_pull(self, repo_or_number, number=None):
        number = repo_or_number if number is None else number
        self.calls.append(("close_pull", number))
        found = self.pulls[number]
        found.state, found.closed_at = "closed", self.now
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

    def results(self, repo, run_id):
        run = self.runs_by_id.get(run_id)
        return None if run is None else run.results()

    def default_branch(self, repo):
        return self.main_branch

    def branch_runs(self, repo, branch):
        return list(self.main_runs) if branch == self.main_branch else []

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
        """The latest completed run of each workflow on the default branch; the fake drops skipped runs, as the real
        hub reads past them."""
        found = []
        for flow in self.flows:
            on_default = [run for run, branch in flow.runs
                          if branch == self.main_branch and not watch.skipped(run)]
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


def stamp(base, seconds):
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def junit_xml(cases):
    body = ""
    for classname, name, kind in cases:
        child = {"failed": "<failure message='boom'/>", "skipped": "<skipped message='why'/>"}.get(kind, "")
        body += f"<testcase classname='{classname}' name='{name}' time='0.5'>{child}</testcase>"
    return f"<testsuites><testsuite name='pytest' tests='{len(cases)}'>{body}</testsuite></testsuites>"


@dataclass
class DispatchedJob:
    """What a dispatched job does: its runner label, the seconds after the dispatch it is queued, starts and
    completes (completed None: still running), its conclusion and the cases of its JUnit report (None: no
    artifact)."""

    label: str = "ubuntu-latest"
    queued: int = 0
    started: int = 130
    completed: int | None = 400
    conclusion: str = "success"
    cases: list | None = field(default_factory=lambda: [("tests.test_a", "test_one", "passed")])


class FakeActions:
    """GitHub's Actions API as the `gh` helper sees it, for one account: its repos, their runs and jobs, the refs
    of the layer's branches and a dispatch of `tests.yml` that makes a run of `dispatch_jobs`. A call is answered
    with the JSON text GitHub would give; a refusal is a `GithubError`."""

    def __init__(self, tmp_path, repo, dispatched_at, *, jobs=None, now=None):
        self.repo = repo
        self.at = dispatched_at  # the stamps of a dispatched run count from here
        self.now = now or dispatched_at  # the stamps of the account's other jobs (`hold`) count back from here
        self.zips = Path(tmp_path) / "zips"
        self.zips.mkdir()
        self.repos = [repo]
        self.runs: dict[str, list[dict]] = {repo: []}
        self.jobs: dict[int, list[dict]] = {}
        self.artifacts: dict[int, list[dict]] = {}
        self.dispatch_jobs = jobs or {}
        self.run_status = "in_progress"  # a dispatched run's own status: GitHub leaves it so long after its jobs end
        self.run_updated = 1100  # seconds after the dispatch the run's own record was last updated
        self.enabled = True
        self.private = False  # the repo's `private` flag
        self.billing = {"total_minutes_used": 0, "included_minutes": 2000, "total_paid_minutes_used": 0}  # the account's minutes
        self.core = (5000, 5000)  # the core quota `api rate_limit` reports: (remaining, limit), a test sets it
        self.known_shas = None  # None: every sha exists on GitHub
        self.refs: dict[str, str] = {}
        self.deleted: list[str] = []
        self.cancelled: list[int] = []
        self.calls: list[list[str]] = []
        self._ids = 7000

    def _next(self):
        self._ids += 1
        return self._ids

    # building the account

    def hold(self, other_repo, running, *, queued=0, queued_for=0):
        """`other_repo` of the account runs `running` jobs and has `queued` jobs waiting for `queued_for` seconds."""

        if other_repo not in self.repos:
            self.repos.append(other_repo)
            self.runs[other_repo] = []
        run_id = self._next()
        created = self.now - timedelta(seconds=queued_for + 60)
        self.runs[other_repo].append(self._run(run_id, "in_progress", None, "pull_request", "feat/x", "o1", created))
        jobs = [self._job(self._next(), f"job{n}", "in_progress", None, "ubuntu-latest", created, 5, None)
                for n in range(running)]
        jobs += [self._job(self._next(), f"wait{n}", "queued", None, "ubuntu-latest", created, None, None)
                 for n in range(queued)]
        self.jobs[run_id] = jobs
        return run_id

    def hold_jobs(self, other_repo, labels, *, status="in_progress", queued_for=0):
        """One run of `other_repo` with one `status` job per entry of `labels` (a runner label or a list of them)."""

        if other_repo not in self.repos:
            self.repos.append(other_repo)
            self.runs[other_repo] = []
        run_id = self._next()
        created = self.now - timedelta(seconds=queued_for + 60)
        self.runs[other_repo].append(self._run(run_id, status, None, "pull_request", "feat/x", "o1", created))
        self.jobs[run_id] = [self._job(self._next(), f"job{n}", status, None, label, created, 5, None)
                             for n, label in enumerate(labels)]
        return run_id

    @staticmethod
    def _run(run_id, status, conclusion, event, branch, sha, created):
        return {"id": run_id, "status": status, "conclusion": conclusion, "event": event, "head_branch": branch,
                "head_sha": sha, "run_attempt": 1, "created_at": stamp(created, 0),
                "updated_at": stamp(created, 0), "pull_requests": []}

    @staticmethod
    def _job(job_id, name, status, conclusion, label, created, started, completed):
        def at(seconds):
            return None if seconds is None else stamp(created, seconds)

        return {"id": job_id, "name": name, "status": status, "conclusion": conclusion, "run_attempt": 1,
                "labels": [label] if isinstance(label, str) else list(label),
                "created_at": at(0), "started_at": at(started), "completed_at": at(completed),
                "steps": [{"name": "pytest", "started_at": at(started and started + 30),
                           "completed_at": at(completed and completed - 30)}]}

    # what GitHub answers

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "run":
            return self._download(args)
        if args[1:] == ["rate_limit"]:
            remaining, limit = self.core
            return json.dumps({"resources": {"core": {"remaining": remaining, "limit": limit}}})
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
        endpoint = next(a for a in args[1:] if a.startswith(("repos/", "user/", "users/")))
        fields = {a.partition("=")[0]: a.partition("=")[2] for a in args[1:] if "=" in a and a != endpoint}
        path, _, query = endpoint.partition("?")
        asked = dict(part.partition("=")[::2] for part in query.split("&") if part)
        parts = path.split("/")
        if path == "user/repos":
            return self._pages(args, asked, None, [{"full_name": r, "owner": {"login": r.split("/")[0]},
                                                    "archived": False} for r in self.repos])
        if path == f"users/{self.repo.split('/')[0]}/settings/billing/actions":
            return json.dumps(self.billing)
        repo, rest = "/".join(parts[1:3]), "/".join(parts[3:])
        if rest == "":  # the repo itself
            return json.dumps({"full_name": repo, "private": self.private})
        if rest == "actions/permissions":
            return json.dumps({"enabled": self.enabled})
        if rest == "git/refs" and method == "POST":
            if self.known_shas is not None and fields["sha"] not in self.known_shas:
                raise GithubError("gh_failed", "422 Object does not exist")
            self.refs[fields["ref"].removeprefix("refs/heads/")] = fields["sha"]
            return "{}"
        if rest.startswith("git/refs/heads/") and method == "DELETE":
            self.deleted.append(rest.removeprefix("git/refs/heads/"))
            return ""
        if rest == "actions/workflows/tests.yml/dispatches":
            self._dispatch(repo, fields["ref"])
            return ""
        if rest == "actions/workflows/tests.yml/runs":
            return json.dumps({"workflow_runs": [r for r in self.runs[repo] if r["head_sha"] == asked["head_sha"]]})
        if rest == "actions/runs":
            return self._pages(args, asked, "workflow_runs",
                               [r for r in self.runs[repo] if r["status"] == asked["status"]])
        run_id, tail = int(parts[5]), (parts[6] if len(parts) > 6 else "")
        if tail == "":
            return json.dumps(next(r for r in self.runs[repo] if r["id"] == run_id))
        if tail == "jobs":
            return self._pages(args, asked, "jobs", self.jobs[run_id])
        if tail == "artifacts":
            return json.dumps({"artifacts": self.artifacts.get(run_id, [])})
        if tail == "timing":
            return json.dumps({"billable": {}})
        if tail == "cancel":
            self.cancelled.append(run_id)
            return ""
        raise AssertionError(args)

    @staticmethod
    def _pages(args, asked, key, items):
        """What `gh api` prints for a list: the page asked for (`page`, `per_page`; GitHub's default is 30 items), or,
        with `--paginate`, every page one after the other."""

        size = int(asked.get("per_page", 30))
        chunks = [items[i:i + size] for i in range(0, len(items), size)] or [[]]
        if "--paginate" not in args:
            chunks = chunks[int(asked.get("page", 1)) - 1:][:1] or [[]]
        return "".join(json.dumps(chunk if key is None else {"total_count": len(items), key: chunk})
                       for chunk in chunks)

    def _dispatch(self, repo, ref):
        run_id, sha = self._next(), self.refs[ref]
        run = self._run(run_id, self.run_status, None, "workflow_dispatch", ref, sha, self.at)
        run["updated_at"] = stamp(self.at, self.run_updated)
        self.runs[repo].append(run)
        self.jobs[run_id] = []
        for name, spec in self.dispatch_jobs.items():
            done = spec.completed is not None
            created = self.at + timedelta(seconds=spec.queued)
            self.jobs[run_id].append(self._job(
                self._next(), name, "completed" if done else "in_progress", spec.conclusion if done else None,
                spec.label, created, spec.started - spec.queued, spec.completed - spec.queued if done else None))
            if spec.cases is not None and done:
                artifact = f"junit-{name}-1"
                self.artifacts.setdefault(run_id, []).append({"name": artifact, "expired": False})
                with zipfile.ZipFile(self.zips / f"{run_id}-{artifact}.zip", "w") as z:
                    z.writestr("junit.xml", junit_xml(spec.cases))

    def _download(self, args):
        run_id, name, folder = int(args[2]), args[args.index("-n") + 1], Path(args[args.index("-D") + 1])
        source = self.zips / f"{run_id}-{name}.zip"
        if not source.exists():
            raise GithubError("gh_failed", f"no artifact {name}")
        with zipfile.ZipFile(source) as z:
            z.extractall(folder)
        return ""


def ago(hub, **delta):
    return hub.now - timedelta(**delta)


__all__ = ["DispatchedJob", "FakeActions", "FAILED", "NO_RESULT", "PASSED", "PENDING", "REPO", "REQUIRED", "Comment", "FakeGitHub", "ago"]
