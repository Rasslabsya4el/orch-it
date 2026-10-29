"""The `github` test place (#414 item 2): the required suite of the `tests` workflow run on GitHub for a commit.

`GithubPlace.run` makes a branch at the commit, dispatches `tests.yml` on it, waits for the required jobs of that
one run, reads them through the importer (`github.import_one_run`, so the jobs' events are the ones any import
writes) and hands the runner one JUnit report merged from the jobs' reports. The time it reports is the jobs'
queueing to their end, never the run's own status, which GitHub leaves in progress long after its jobs have ended.

`availability` is what the place rule cannot read from the store: whether Actions is on, whether the Actions request
budget has room, and how many of the account's machines are busy (every repo of the account shares them, so the
count is the account's, not this repo's). A job that has waited in the queue while others run shows the limit the
account really has; else the limit is the setting `github_capacity`. Any `gh` failure leaves the place off.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr

from orch_it.events import github_budget
from orch_it.events.store import Store
from orch_it.testruns import junit
from orch_it.testruns.config import OSES, TestSettings
from orch_it.testruns.github import (WORKFLOW_FILE, Gh, GithubError, import_one_run, items_of, json_values,
                                     moment_of, os_of, run_gh)
from orch_it.testruns.places import Availability, PlaceEnd, PlaceRefused, PlaceVanished, Request

POLL_SECONDS = 20.0  # between two reads of a run's jobs
CACHE_SECONDS = 60.0  # an availability reading stands this long
QUEUE_GRACE_SECONDS = 120.0  # a job queued longer than this while others run shows the account's limit
REQUEST_ESTIMATE = 40  # Actions requests one availability reading and one run may make, asked of the budget
PER_PAGE = 100
REF_PREFIX = "orch-it-run-"
RED = ("failed", "error")
GOOD_CONCLUSIONS = ("success", "skipped")
STORED_OS_QUERY = ("SELECT DISTINCT run_os FROM events WHERE kind = 'step.started' AND step = 'test_run'"
                   " AND role = 'github' AND repo = ? AND run_os IS NOT NULL")


def oses_of_workflow(text: str, required: tuple[str, ...]) -> tuple[str, ...]:
    """The OSes the required checks run on, in the order of `required`: each job's `runs-on` read by a line scan (no
    YAML parser in the dependencies), its first label that names an OS. A job is found by its `name`, else its key; a
    label that is no OS word (a `${{ matrix }}` expression, `self-hosted`) gives none."""

    jobs = _jobs_of(text)
    found: list[str] = []
    for check in required:
        for job in jobs:
            if (job["name"] or job["key"]) != check:
                continue
            os_name = next((o for o in (os_of({"labels": [label]}) for label in job["labels"]) if o), None)
            if os_name and os_name not in found:
                found.append(os_name)
    return tuple(found)


def _jobs_of(text: str) -> list[dict]:
    """The jobs of a workflow's `jobs:` section: `key`, `name` (or None) and `labels` (the `runs-on` labels)."""

    jobs: list[dict] = []
    in_jobs, job_indent, child_indent, listing = False, None, None, False
    for raw in text.splitlines():
        line = raw.split(" #")[0].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_jobs, job_indent, child_indent, listing = stripped == "jobs:", None, None, False
            continue
        if not in_jobs:
            continue
        if job_indent is None:
            job_indent = indent
        if indent == job_indent:
            jobs.append({"key": _unquote(stripped.partition(":")[0]), "name": None, "labels": []})
            child_indent, listing = None, False
            continue
        if not jobs:
            continue
        if child_indent is None:
            child_indent = indent
        job = jobs[-1]
        if listing and stripped.startswith("-"):
            job["labels"].append(_unquote(stripped[1:].strip()))
            continue
        listing = False
        if indent != child_indent:
            continue
        key, _, value = stripped.partition(":")
        value = value.strip()
        if key == "name":
            job["name"] = _unquote(value)
        elif key == "runs-on":
            if value:
                job["labels"] += [_unquote(v.strip()) for v in value.strip("[]").split(",") if v.strip()]
            else:
                listing = True
    return jobs


def _unquote(text: str) -> str:
    text = text.strip()
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"" else text


def _is_machine_of_the_limit(job: dict) -> bool:
    """A job on a GitHub-hosted Linux or Windows runner: the machines the account's limit is about (a self-hosted
    runner or a macOS one is not counted against it)."""

    labels = [label for label in job.get("labels") or [] if isinstance(label, str)]
    hosted = "self-hosted" not in (label.lower() for label in labels)
    os_name = os_of({"labels": labels})
    return hosted and os_name is not None and os_name != "macos"


def _stored_oses(store: Store, repo: str) -> tuple[str, ...]:
    """The OSes of the github-role test runs the store holds for `repo`, in the order of the closed list."""

    held = {os_name for (os_name,) in store.query(STORED_OS_QUERY, (repo,)).rows}
    return tuple(os_name for os_name in OSES if os_name in held)


class GithubPlace:
    """The place `github`. `oses`: the OSes its required jobs run on (see `for_work_tree`)."""

    id = "github"

    def __init__(self, repo: str, store: Store, settings: TestSettings, gh: Gh = run_gh,
                 oses: tuple[str, ...] = (), sleep=time.sleep, clock=time.monotonic):
        self.repo, self.store, self.settings, self.gh, self.oses = repo, store, settings, gh, oses
        self.sleep, self.clock = sleep, clock  # injectable: the run's bound is counted on `clock`
        self._cached: tuple[datetime, Availability] | None = None

    @classmethod
    def for_work_tree(cls, work_tree: Path, *, repo: str, store: Store, settings: TestSettings, gh: Gh = run_gh,
                      workflow_text: str | None = None) -> GithubPlace:
        """The place with the OSes of `settings.required_checks` read from the work tree's `tests.yml` (or the given
        text); with none readable there, the OSes of the github-role runs the store holds."""

        if workflow_text is None:
            path = Path(work_tree) / ".github" / "workflows" / WORKFLOW_FILE
            try:
                workflow_text = path.read_text("utf-8")
            except OSError:
                workflow_text = ""
        oses = oses_of_workflow(workflow_text, settings.required_checks) or _stored_oses(store, repo)
        return cls(repo, store, settings, gh, oses)

    @property
    def os(self) -> str | None:
        """The one OS the place runs on, None when it runs on several (or none is known)."""

        return self.oses[0] if len(self.oses) == 1 else None

    # --- what the rule cannot read from the store ----------------------------------------------------

    def availability(self, now: datetime) -> Availability:
        cached = self._cached
        if cached is not None and timedelta(0) <= now - cached[0] < timedelta(seconds=CACHE_SECONDS):
            return cached[1]
        reading = self._read(now)
        self._cached = (now, reading)
        return reading

    def _read(self, now: datetime) -> Availability:
        capacity = self.settings.github_capacity
        try:
            permissions = self._object(self.gh(["api", f"repos/{self.repo}/actions/permissions"]))
            if permissions.get("enabled") is not True:
                return Availability(False, 0, capacity, "Actions is off")
            if github_budget.would_cross(self.repo, REQUEST_ESTIMATE, store=self.store, now=now):
                return Availability(False, 0, capacity, "Actions budget would cross its threshold")
            busy, waited = self._account_load(now)
        except GithubError as e:
            return Availability(False, 0, capacity, f"GitHub could not be read ({e.code})")
        limit = busy if waited and busy else capacity
        return Availability(True, busy, limit, f"{busy} busy of {limit}")

    def _account_load(self, now: datetime) -> tuple[int, bool]:
        """The account's jobs in progress, and whether a queued job has waited past the grace while others run."""

        owner = self.repo.split("/")[0].lower()
        listed = items_of(self.gh(["api", "--paginate", f"user/repos?per_page={PER_PAGE}"]), "repos")
        busy, waiting = 0, False
        for entry in listed:
            full = entry.get("full_name")
            if (not isinstance(full, str) or entry.get("archived")
                    or str((entry.get("owner") or {}).get("login", "")).lower() != owner):
                continue
            for status in ("in_progress", "queued"):
                runs = items_of(self.gh(["api", "--paginate",
                                         f"repos/{full}/actions/runs?status={status}&per_page={PER_PAGE}"]),
                                "workflow_runs")
                for run in runs:
                    jobs = items_of(self.gh(["api", "--paginate", f"repos/{full}/actions/runs/{run['id']}/jobs"
                                                    f"?filter=latest&per_page={PER_PAGE}"]), "jobs")
                    for job in jobs:
                        if not _is_machine_of_the_limit(job):
                            continue
                        if job.get("status") == "in_progress":
                            busy += 1
                        elif job.get("status") == "queued":
                            queued_at = moment_of(job.get("created_at"))
                            if queued_at is not None and (now - queued_at).total_seconds() >= QUEUE_GRACE_SECONDS:
                                waiting = True
        return busy, waiting

    @staticmethod
    def _object(text: str) -> dict:
        values = json_values(text)
        return values[0] if values and isinstance(values[0], dict) else {}

    # --- one run ----------------------------------------------------------------------------------

    def run(self, request: Request, scratch: Path) -> PlaceEnd:
        if request.ids is not None:  # the dispatch runs the whole workflow: it cannot run a selection of ids
            raise PlaceRefused("place_off", "a selection of ids cannot run on GitHub")
        if not request.commit:
            raise PlaceRefused("place_off", "no commit to run", step="commit")
        ref = f"{REF_PREFIX}{uuid.uuid4().hex[:12]}"
        try:
            self._make_ref(ref, request.commit)
            try:
                return self._run_on(ref, request, scratch)
            finally:
                self._drop_ref(ref)
        except GithubError as e:  # the run was lost on the way: GitHub stopped answering
            raise PlaceVanished(str(e)) from None

    def _make_ref(self, ref: str, sha: str) -> None:
        try:
            self.gh(["api", "-X", "POST", f"repos/{self.repo}/git/refs", "-f", f"ref=refs/heads/{ref}", "-f",
                     f"sha={sha}"])
        except GithubError as e:
            raise PlaceRefused("place_off", str(e), step="commit") from None

    def _drop_ref(self, ref: str) -> None:
        try:
            self.gh(["api", "-X", "DELETE", f"repos/{self.repo}/git/refs/heads/{ref}"])
        except GithubError:
            pass  # a branch left behind costs nothing; the run's own result stands

    def _run_on(self, ref: str, request: Request, scratch: Path) -> PlaceEnd:
        deadline = self.clock() + request.bound_seconds
        try:
            self.gh(["api", "-X", "POST", f"repos/{self.repo}/actions/workflows/{WORKFLOW_FILE}/dispatches", "-f",
                     f"ref={ref}"])
        except GithubError as e:
            raise PlaceRefused("place_off", str(e), step="connect") from None
        run_id = self._find_run(ref, request.commit, deadline)
        if run_id is None:  # a run that shows up just after the bound is still ours to stop
            run_id = self._ours(ref, request.commit)
            if run_id is not None:
                self._cancel(run_id)
            return self._cut(run_id, request)
        jobs = self._wait_for_jobs(run_id, deadline)
        if jobs is None:
            self._cancel(run_id)
            return self._cut(run_id, request)
        present = self._present(jobs)
        if any(job.get("conclusion") == "cancelled" for job in present):
            raise PlaceVanished(f"a job of run {run_id} was cancelled")
        return self._finish(run_id, present, scratch)

    def _pause(self, deadline: float) -> bool:
        """One poll interval slept, never past the deadline; False when the deadline has passed."""

        left = deadline - self.clock()
        if left <= 0:
            return False
        self.sleep(min(POLL_SECONDS, left))
        return True

    def _ours(self, ref: str, sha: str) -> int | None:
        """The id of the run the dispatch made, the one on our own branch; None while none is listed."""

        runs = items_of(self.gh(["api", f"repos/{self.repo}/actions/workflows/{WORKFLOW_FILE}/runs"
                                        f"?head_sha={sha}&per_page={PER_PAGE}"]), "workflow_runs")
        ours = [run["id"] for run in runs if run.get("head_branch") == ref]
        return max(ours) if ours else None

    def _find_run(self, ref: str, sha: str, deadline: float) -> int | None:
        """The run the dispatch made, waited for until the deadline; None when it passes first."""

        while True:
            run_id = self._ours(ref, sha)
            if run_id is not None:
                return run_id
            if not self._pause(deadline):
                return None

    def _present(self, jobs: list[dict]) -> list[dict]:
        return [job for job in jobs if isinstance(job.get("name"), str)
                and job["name"].strip() in self.settings.required_checks]

    def _wait_for_jobs(self, run_id: int, deadline: float) -> list[dict] | None:
        """The run's jobs once every present required job has completed; None at the bound."""

        while True:
            jobs = items_of(self.gh(["api", f"repos/{self.repo}/actions/runs/{run_id}/jobs"
                                            f"?filter=latest&per_page={PER_PAGE}"]), "jobs")
            present = self._present(jobs)
            if present and all(job.get("status") == "completed" for job in present):
                return jobs
            if not present and self._run_completed(run_id):
                return jobs
            if any(job.get("conclusion") == "cancelled" for job in present):
                return jobs
            if not self._pause(deadline):
                return None

    def _run_completed(self, run_id: int) -> bool:
        run = self._object(self.gh(["api", f"repos/{self.repo}/actions/runs/{run_id}"]))
        return run.get("status") == "completed"

    def _cancel(self, run_id: int) -> None:
        try:
            self.gh(["api", "-X", "POST", f"repos/{self.repo}/actions/runs/{run_id}/cancel"])
        except GithubError:
            pass  # the run is cut either way; GitHub's own timeout ends it

    @staticmethod
    def _cut(run_id: int | None, request: Request) -> PlaceEnd:
        tail = "no run appeared for the dispatch" if run_id is None else f"run {run_id} reached the bound"
        return PlaceEnd(None, True, False, None, 0.0, request.bound_seconds, tail)

    def _finish(self, run_id: int, present: list[dict], scratch: Path) -> PlaceEnd:
        kept = scratch / "reports"
        kept.mkdir(parents=True, exist_ok=True)
        import_one_run(self.store, self.repo, run_id, gh=self.gh, now=datetime.now(UTC),
                       required=self.settings.required_checks, keep=kept, timed=False)
        cases, readable = [], False
        for path in sorted(kept.glob("*.xml")):
            try:
                cases += junit.read(path)
                readable = True
            except junit.ReportUnreadable:
                continue
        names = {job["name"].strip() for job in present}
        unsound = (any(job.get("conclusion") not in GOOD_CONCLUSIONS for job in present)
                   or any(name not in names for name in self.settings.required_checks))
        report = None
        if readable and (any(case.outcome in RED for case in cases) or not unsound):
            report = scratch / "junit.xml"
            report.write_text(_junit_text(cases), "utf-8")
        tail = ", ".join(f"{job['name'].strip()}: {job.get('conclusion')}" for job in present)
        return PlaceEnd(report, False, False, None, 0.0, _queued_to_finished(present), tail)


def _queued_to_finished(jobs: list[dict]) -> float:
    """Seconds from the earliest queueing to the latest end among the jobs."""

    created = [moment_of(job.get("created_at")) for job in jobs]
    ended = [moment_of(job.get("completed_at")) for job in jobs]
    created, ended = [m for m in created if m], [m for m in ended if m]
    if not created or not ended:
        return 0.0
    return max((max(ended) - min(created)).total_seconds(), 0.0)


def _junit_text(cases: list[junit.Case]) -> str:
    """One JUnit report holding `cases`, which `junit.read` reads back as the same cases."""

    rows = []
    for case in cases:
        child = {"failed": "<failure message='failed'/>", "error": "<error message='error'/>",
                 "skipped": f"<skipped message={quoteattr(case.skip_reason or '')}/>"}.get(case.outcome, "")
        seconds = "" if case.seconds is None else f" time={quoteattr(repr(case.seconds))}"
        rows.append(f"<testcase classname={quoteattr(case.classname)} name={quoteattr(case.name)}{seconds}>"
                    f"{child}</testcase>")
    return (f"<testsuites><testsuite name='github' tests='{len(cases)}'>{''.join(rows)}</testsuite></testsuites>")
