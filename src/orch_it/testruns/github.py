"""The GitHub importer (#394 decision 7): the runs of the `tests` workflow become events.

For each completed run, and for each job named in the setting `github_required_jobs` (default
`linux` and `windows`) in every attempt of it, the importer writes a `test_run` step pair
(place `github`), the queue time as a `wait` pair with reason `github_queue`, and one
`test.result` per case of the job's JUnit artifact. The artifact is read by `outcome.judge`
like any other report; the job's GitHub conclusion decides the outcome:

- `success`: passed, always (the report only gives the rows and the counts);
- `failure`: failed `red` when the report holds a red case, else no_result `summary_missing`;
- `cancelled`: stopped `superseded` when a newer run of the workflow on the same branch was
  created after it (and before `now`), else stopped with no reason;
- `timed_out`: no_result `cut`;
- `skipped`: no test run at all, a skipped job is not a run;
- anything else (`neutral`, `stale`, `startup_failure`, `action_required`): no_result
  `summary_missing`.

A completed run with no job of a required name in its latest attempt gets, for that name, a
`job_missing` pair. A run still in progress is left for the next import. Every event's id is a
hash of its record, so a second import writes nothing; a job whose `step.ended` is stored is
skipped before anything is downloaded. A run's events are written only once everything the run
needs from `gh` has been read, and its `test.result` rows before its `step.ended`, so a run
`gh` failed on leaves nothing behind and an interrupted import is finished by the next one.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from orch_it import settings
from orch_it.events.ids import import_id
from orch_it.events.store import Store
from orch_it.testruns import outcome

SOURCE = "github"
WORKFLOW_FILE = "tests.yml"
REQUIRED_JOBS_SETTING = "github_required_jobs"
DEFAULT_REQUIRED_JOBS = ["linux", "windows"]
TEST_STEP = "pytest"  # the step of a job that runs the tests
REPORT_FILE = "junit.xml"
NIGHTLY_EVENTS = ("schedule", "workflow_dispatch")
API_TIMEOUT_S = 120
DOWNLOAD_TIMEOUT_S = 300
ERROR_TEXT_CHARS = 300
GH_ERROR_CODES = ("gh_failed", "gh_timeout", "gh_bad_output", "bad_since", "bad_now")

Gh = Callable[[list[str]], str]


class GithubError(Exception):
    """`code`: `gh_failed` (`gh` is missing or answered with a failure), `gh_timeout` (it did
    not answer in time), `gh_bad_output` (its answer is no JSON of the shape asked for),
    `bad_since`, `bad_now` (an argument that is no time, or has no zone)."""

    def __init__(self, code: str, detail: str = ""):
        if code not in GH_ERROR_CODES:
            raise ValueError(f"not a github error code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass
class ImportReport:
    """`runs_read`: runs created since `since`, in progress ones too. `jobs`: test runs looked
    at, written or already stored (skipped jobs are none, a missing job is one). `written`,
    `already_stored`: events. `reports_missing`: jobs read that had no JUnit artifact to
    download (never uploaded, expired or listed under no name asked for)."""

    runs_read: int = 0
    jobs: int = 0
    written: int = 0
    already_stored: int = 0
    reports_missing: int = 0


def run_gh(args: list[str]) -> str:
    """The default `gh` runner: `gh <args>` bounded by a timeout, its stdout as text."""

    timeout = DOWNLOAD_TIMEOUT_S if args[:1] == ["run"] else API_TIMEOUT_S
    try:
        done = subprocess.run(
            ["gh", *args], capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
            env={**os.environ, "GH_PROMPT_DISABLED": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise GithubError("gh_timeout", " ".join(args[:3])) from None
    except OSError as e:
        raise GithubError("gh_failed", f"gh could not be started: {e}") from None
    if done.returncode != 0:
        said = done.stderr.decode("utf-8", "replace").strip()[:ERROR_TEXT_CHARS]
        raise GithubError("gh_failed", f"{' '.join(args[:3])}: {said}")
    return done.stdout.decode("utf-8", "replace")


def import_github(store: Store, repo: str, *, gh: Gh | None = None, since: str | None = None,
                  now: datetime | None = None) -> ImportReport:
    """Write the events of `repo`'s `tests` workflow runs the store does not hold yet.

    `gh` takes the argument list of one `gh` call and returns its stdout (default: the real
    CLI). `since`: only runs created at or after it (ISO 8601, a zone-less one is UTC). `now`
    bounds which runs can have superseded a cancelled one. Bad arguments raise before `gh` is
    called."""

    gh = gh or run_gh
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise GithubError("bad_now", str(now))
    floor = _since(since)
    required = _required_jobs()

    every_run = _items(gh(["api", "--paginate",
                           f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=100"]),
                       "workflow_runs")
    runs = sorted((r for r in every_run if floor is None or _created(r) >= floor),
                  key=lambda r: (_created(r), r.get("id", 0)))
    report = ImportReport(runs_read=len(runs))
    importer = _Importer(store, repo, gh, required, every_run, now, report)
    with store.writer() as writer:
        for run in runs:
            if run.get("status") == "completed":
                importer.import_run(run, writer)
    return report


def _since(since: str | None) -> datetime | None:
    if since is None:
        return None
    try:
        moment = datetime.fromisoformat(since)
    except (TypeError, ValueError):
        raise GithubError("bad_since", str(since)) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _required_jobs() -> list[str]:
    names = settings.load().get(REQUIRED_JOBS_SETTING, DEFAULT_REQUIRED_JOBS)
    if (not isinstance(names, list) or not names
            or not all(isinstance(n, str) and n.strip() for n in names)):
        raise settings.SettingsError("bad_setting", REQUIRED_JOBS_SETTING)
    return list(dict.fromkeys(names))


def _json_values(text: str) -> list:
    """The JSON values `gh api --paginate` printed one after the other (one per page)."""

    decoder, values, at = json.JSONDecoder(), [], 0
    while True:
        while at < len(text) and text[at].isspace():
            at += 1
        if at >= len(text):
            return values
        try:
            value, at = decoder.raw_decode(text, at)
        except ValueError:
            raise GithubError("gh_bad_output", text[at:at + 80]) from None
        values.append(value)


def _items(text: str, key: str) -> list[dict]:
    items = []
    for page in _json_values(text):
        found = page.get(key) if isinstance(page, dict) else page
        if not isinstance(found, list):
            raise GithubError("gh_bad_output", f"no list `{key}`")
        items += [item for item in found if isinstance(item, dict)]
    return items


def _moment(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _created(run: dict) -> datetime:
    return _moment(run.get("created_at")) or datetime.min.replace(tzinfo=UTC)


def _local(moment: datetime) -> str:
    return moment.astimezone().isoformat(timespec="microseconds")


def _minutes(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0) / 60


def _os_of(job: dict) -> str | None:
    """`linux`, `windows` or `macos`, from the job's labels, else its name."""

    labels = [l for l in job.get("labels") or [] if isinstance(l, str)]
    for text in [*labels, str(job.get("name") or "")]:
        text = text.lower()
        if "ubuntu" in text or "linux" in text:
            return "linux"
        if "windows" in text:
            return "windows"
        if "macos" in text or text.startswith("mac"):
            return "macos"
    return None


@dataclass
class _Target:
    """One test run to write: a real job (`job` set) or a required job the run lacks."""

    key: str  # `<run id>/<job id or job name>/<attempt>`
    name: str
    attempt: int
    job: dict | None

    @property
    def run(self) -> str:
        return f"{SOURCE}:{self.key}"

    def id(self, kind: str) -> str:
        return import_id(SOURCE, self.key, kind)

    def known_ids(self) -> list[str]:
        return [self.id(k) for k in ("step.started", "step.ended", "wait.started", "wait.ended")]


class _Importer:
    def __init__(self, store, repo, gh, required, every_run, now, report):
        self.store, self.repo, self.gh = store, repo, gh
        self.required, self.now, self.report = required, now, report
        self.created = {r.get("id"): (r.get("head_branch"), _moment(r.get("created_at")))
                        for r in every_run}

    def import_run(self, run: dict, writer) -> None:
        run_id, attempt = run["id"], run.get("run_attempt") or 1
        jobs = _items(self.gh(["api", "--paginate",
                               f"repos/{self.repo}/actions/runs/{run_id}/jobs?filter=all&per_page=100"]),
                      "jobs")
        targets, present = [], set()
        for job in jobs:
            name, job_attempt = job.get("name"), job.get("run_attempt") or attempt
            if name not in self.required:
                continue
            if job_attempt == attempt:
                present.add(name)
            if job.get("conclusion") != "skipped":
                targets.append(_Target(f"{run_id}/{job['id']}/{job_attempt}", name, job_attempt, job))
        targets += [_Target(f"{run_id}/{name}/{attempt}", name, attempt, None)
                    for name in self.required if name not in present]

        stored = self.store.existing_ids([i for t in targets for i in t.known_ids()])
        pending = []
        for target in targets:
            self.report.jobs += 1
            if target.id("step.ended") in stored:
                self.report.already_stored += sum(i in stored for i in target.known_ids())
            else:
                pending.append(target)
        if not pending:
            return

        events = self._events(run, pending)
        stored = self.store.existing_ids([e["id"] for e in events])
        self.report.already_stored += len(stored)
        for event in events:
            if event["id"] not in stored:
                writer.write(event)
                self.report.written += 1

    # --- reading what a run needs from gh --------------------------------------------------

    def _events(self, run: dict, pending: list[_Target]) -> list[dict]:
        run_id = run["id"]
        listed, billed = {}, {}
        if any(t.job is not None for t in pending):
            artifacts = _items(self.gh(["api", "--paginate",
                                        f"repos/{self.repo}/actions/runs/{run_id}/artifacts?per_page=100"]),
                               "artifacts")
            listed = {a.get("name") for a in artifacts if not a.get("expired")}
            billed = self._billed(run_id)
        events = []
        for target in pending:
            events += self._target_events(run, target, listed, billed)
        return events

    def _billed(self, run_id: int) -> dict[int, int]:
        """Job id -> billable minutes (a job is billed by the started minute: rounded up)."""

        timing = _json_values(self.gh(["api", f"repos/{self.repo}/actions/runs/{run_id}/timing"]))
        found = {}
        for page in timing:
            for system in (page.get("billable") or {}).values() if isinstance(page, dict) else []:
                for job in (system.get("job_runs") or []) if isinstance(system, dict) else []:
                    ms = job.get("duration_ms") if isinstance(job, dict) else None
                    if isinstance(ms, (int, float)) and "job_id" in job:
                        found[job["job_id"]] = math.ceil(ms / 60000)
        return found

    def _judge(self, run: dict, target: _Target, listed: set) -> tuple[outcome.RunEnd, bool]:
        """How the job's report reads, and whether it had one to download."""

        job = target.job
        conclusion = job.get("conclusion")
        name = next((n for n in (f"junit-{target.name}-{target.attempt}", f"junit-{target.name}")
                     if n in listed), None)
        cut = conclusion == "timed_out"
        if name is None:
            return outcome.judge(None, cut=cut), False
        with tempfile.TemporaryDirectory(prefix="orch-it-gh-") as folder:
            self.gh(["run", "download", str(run["id"]), "-R", self.repo, "-n", name, "-D", folder])
            report = next(iter(sorted(Path(folder).rglob(REPORT_FILE))), None)
            return outcome.judge(report, cut=cut), report is not None

    # --- the events of one job -------------------------------------------------------------

    def _target_events(self, run: dict, target: _Target, listed: set, billed: dict) -> list[dict]:
        job = target.job
        created = _moment(run.get("created_at"))
        if job is None:  # a required job the run never had
            end = outcome.RunEnd("no_result", "job_missing", (), False)
            started_at, ended_at = created, _moment(run.get("updated_at")) or created
            run_os = _os_of({"name": target.name})
            minutes = {"billed_minutes": 0, "start_minutes": None, "run_minutes": None}
            wait = None
        else:
            end, found = self._judge(run, target, listed)
            if not found:
                self.report.reports_missing += 1
            end = self._by_conclusion(run, job, end)
            job_created = _moment(job.get("created_at"))
            job_started = _moment(job.get("started_at"))
            started_at = job_started or job_created or created
            ended_at = _moment(job.get("completed_at")) or _moment(run.get("updated_at")) or started_at
            run_os = _os_of(job)
            step = next((s for s in job.get("steps") or [] if s.get("name") == TEST_STEP), {})
            step_start, step_end = _moment(step.get("started_at")), _moment(step.get("completed_at"))
            minutes = {"billed_minutes": billed.get(job.get("id"), 0),
                       "start_minutes": _minutes(job_created, step_start),
                       "run_minutes": _minutes(step_start, step_end)}
            wait = (job_created, job_started) if job_created and job_started else None

        pr = next((p.get("number") for p in run.get("pull_requests") or [] if isinstance(p, dict)), None)
        event_name = run.get("event")
        envelope = {"repo": self.repo, "ticket": None, "role": "github", "session": None,
                    "parent_session": None, "run": target.run, "note": None}
        events = [self._event("step.started", target.id("step.started"), started_at,
                              envelope | {"step": "test_run"}, {
                                  "place": "github", "chosen": "github", "reason": None,
                                  "run_os": run_os, "commit": run.get("head_sha"),
                                  "branch": run.get("head_branch"), "pr": pr,
                                  "selection": "nightly" if event_name in NIGHTLY_EVENTS else "full",
                                  "event": event_name})]
        if wait is not None:
            events += [
                self._event("step.started", target.id("wait.started"), wait[0],
                            envelope | {"step": "wait"},
                            {"reason": "github_queue", "on": target.run}),
                self._event("step.ended", target.id("wait.ended"), wait[1],
                            envelope | {"step": "wait"}, {"outcome": "passed", "reason": None})]
        for number, detail in enumerate(end.result_details(), start=1):
            events.append(self._event("test.result", target.id(f"test.result/{number}"), ended_at,
                                      envelope | {"step": "test_run"}, detail))
        events.append(self._event("step.ended", target.id("step.ended"), ended_at,
                                  envelope | {"step": "test_run"}, end.ended_detail() | minutes))
        return events

    def _by_conclusion(self, run: dict, job: dict, end: outcome.RunEnd) -> outcome.RunEnd:
        conclusion = job.get("conclusion")
        if conclusion == "success":
            return replace(end, outcome="passed", reason=None)
        if conclusion == "failure":
            if end.outcome == "failed":
                return end
        elif conclusion == "cancelled":
            return replace(end, outcome="stopped",
                           reason="superseded" if self._superseded(run) else None)
        elif conclusion == "timed_out":
            return end  # judged as cut
        return replace(end, outcome="no_result", reason="summary_missing")

    def _superseded(self, run: dict) -> bool:
        branch, created = run.get("head_branch"), _moment(run.get("created_at"))
        if branch is None or created is None:
            return False
        return any(other_branch == branch and other_at is not None and created < other_at <= self.now
                   for other_id, (other_branch, other_at) in self.created.items()
                   if other_id != run.get("id"))

    @staticmethod
    def _event(kind: str, event_id: str, at: datetime, envelope: dict, detail: dict) -> dict:
        return {"id": event_id, "kind": kind, "at": _local(at), "turn": None, **envelope,
                "detail": detail}

