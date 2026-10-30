"""The GitHub importer (#394 decision 7; acceptance row 6): the runs of the `tests` workflow
become a `test_run` pair per required job, the queue wait, and one `test.result` per test case.
`gh` is a fake serving a recorded run set (JSON pages and artifact zips under tmp_path), so every
expected number below is read off the fixture's clock: a job is created at the run's base time,
starts 60 s later, its `pytest` step runs from +90 s to +330 s, and it completes at +360 s."""

import json
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orch_it import settings
from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.testruns import github
from orch_it.testruns.github import GithubError, import_github
from orch_it.tickets import hook as ticket_hook
from fake_github import DEFAULT_BRANCH
from required_names import FOREIGN_LINUX, FOREIGN_MACOS, FOREIGN_WINDOWS, LINUX_CHECK, WINDOWS_CHECK

REPO = "acme-x/widgets-y"
BASE = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
GREEN = [("tests.test_a", "test_one", "passed"), ("tests.test_a", "test_two", "passed"),
         ("tests.test_b", "test_three", "skipped")]
RED = [("tests.test_a", "test_one", "passed"), ("tests.test_a", "test_two", "failed")]


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def stamp(base, seconds):
    return None if seconds is None else (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def junit_xml(cases):
    body = ""
    for classname, name, kind in cases:
        child = {"failed": "<failure message='boom'/>", "skipped": "<skipped message='why'/>"}.get(kind, "")
        body += f"<testcase classname='{classname}' name='{name}' time='0.5'>{child}</testcase>"
    return f"<testsuites><testsuite name='pytest' tests='{len(cases)}'>{body}</testsuite></testsuites>"


class FakeGh:
    """Serves what `gh api --paginate` and `gh run download` would, from recorded data."""

    def __init__(self, tmp_path):
        self.zips = tmp_path / "zips"
        self.zips.mkdir()
        self.runs, self.jobs, self.artifacts, self.timing = [], {}, {}, {}
        self.calls = []
        self.fail_when = None

    def __call__(self, args):
        self.calls.append(list(args))
        if self.fail_when and self.fail_when(args):
            raise GithubError("gh_failed", "recorded failure")
        if args[0] == "run":
            return self.download(args)
        path = args[-1].partition("?")[0]
        parts = path.split("/")
        if path.endswith(f"/workflows/{github.WORKFLOW_FILE}/runs"):
            if "--paginate" not in args:
                return self.first_page(args[-1].partition("?")[2])
            return self.pages("workflow_runs", self.runs)
        run_id = int(parts[5])
        if parts[-1] == "jobs":
            return self.pages("jobs", self.jobs[run_id])
        if parts[-1] == "artifacts":
            return self.pages("artifacts", self.artifacts.get(run_id, []))
        if parts[-1] == "timing":
            return json.dumps(self.timing.get(run_id, {"billable": {}}))
        raise AssertionError(args)

    def first_page(self, query):
        """What an unpaginated call answers: `total_count` of the runs matching the filters and the
        newest `per_page` of them, newest first (the API orders by creation)."""

        asked = dict(part.partition("=")[::2] for part in query.split("&"))
        matching = [r for r in self.runs if asked.get("status") in (None, r["status"])
                    and r["created_at"] >= asked.get("created", ">=").removeprefix(">=")]
        newest_first = sorted(matching, key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return json.dumps({"total_count": len(matching),
                           "workflow_runs": newest_first[:int(asked.get("per_page", 30))]})

    @staticmethod
    def pages(key, items):
        chunks = [items[i:i + 2] for i in range(0, len(items), 2)] or [[]]
        return "".join(json.dumps({"total_count": len(items), key: chunk}) for chunk in chunks)

    def download(self, args):
        run_id, name, folder = int(args[2]), args[args.index("-n") + 1], Path(args[args.index("-D") + 1])
        source = self.zips / f"{run_id}-{name}.zip"
        if not source.exists():
            raise GithubError("gh_failed", f"no artifact {name}")
        with zipfile.ZipFile(source) as z:
            z.extractall(folder)
        return ""

    def downloads(self):
        return [c for c in self.calls if c[0] == "run"]

    def add_run(self, run_id, *, jobs=(LINUX_CHECK, WINDOWS_CHECK), created=0, status="completed",
                conclusion="success", event="pull_request", branch="feat/x", pr=7, attempt=1,
                billed=True, sha=None, **per_job):
        """`per_job`: job name -> dict of `conclusion`, `cases` (the JUnit report; None: no
        artifact), `listed` (the artifact is listed), `expired`, `started` (False: never
        started; a number: seconds after creation), `completed` (seconds after creation), `attempt`, `artifact_name`, `billed_ms`, `rate_limit` (the text of
        the artifact's `actions-rate-limit.json`)."""

        base = BASE + timedelta(seconds=created)
        self.runs.append({
            "id": run_id, "status": status, "conclusion": conclusion, "event": event,
            "head_branch": branch, "head_sha": sha or f"sha{run_id}", "run_attempt": attempt,
            "created_at": stamp(base, 0), "updated_at": stamp(base, 400),
            "pull_requests": [{"number": pr}] if pr else []})
        self.jobs[run_id], billable = [], []
        for index, name in enumerate(jobs):
            spec = {"conclusion": "success", "cases": GREEN, "expired": False, "started": True,
                    "attempt": attempt, "billed_ms": 300_000, "completed": 360} | per_job.get(name, {})
            job_id = run_id * 10 + index + 1
            labels = ["ubuntu-latest"] if name == LINUX_CHECK else ["windows-latest"]
            ran = spec["conclusion"] != "skipped"
            self.jobs[run_id].append({
                "id": job_id, "name": name, "status": "completed", "conclusion": spec["conclusion"],
                "run_attempt": spec["attempt"], "labels": labels,
                "created_at": stamp(base, 0),
                "started_at": None if spec["started"] is False else stamp(
                    base, 60 if spec["started"] is True else spec["started"]),
                "completed_at": stamp(base, spec["completed"]),
                "steps": [{"name": "Set up job", "started_at": stamp(base, 60), "completed_at": stamp(base, 62)}]
                + ([{"name": "pytest", "started_at": stamp(base, 90), "completed_at": stamp(base, 330)}]
                   if ran else [])})
            if spec["conclusion"] == "skipped":
                continue
            billable.append({"job_id": job_id, "duration_ms": spec["billed_ms"]})
            artifact = spec.get("artifact_name", f"junit-{name}-{spec['attempt']}")
            if spec["cases"] is not None:
                self.artifacts.setdefault(run_id, []).append(
                    {"name": artifact, "expired": spec["expired"]})
                with zipfile.ZipFile(self.zips / f"{run_id}-{artifact}.zip", "w") as z:
                    z.writestr("junit.xml", junit_xml(spec["cases"]))
                    if spec.get("rate_limit") is not None:  # the workflow step's file (#414 unit 12a)
                        z.writestr("actions-rate-limit.json", spec["rate_limit"])
        if billable and billed:
            self.timing[run_id] = {"billable": {"UBUNTU": {"total_ms": 0, "job_runs": billable}}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)
    (tmp_path / "scratch").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "scratch"))


@pytest.fixture
def gh(tmp_path):
    return FakeGh(tmp_path)


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")


def run_import(store, gh, **kwargs):
    return import_github(store, REPO, gh=gh, now=NOW, **kwargs)


def pairs(store, step="test_run"):
    """run -> (started detail, ended detail) of the step pairs in the store."""

    found = {}
    for run, kind, detail in store.query(
            "SELECT run, kind, detail FROM events WHERE step = ? AND kind IN"
            " ('step.started', 'step.ended') ORDER BY at, kind DESC", (step,)).rows:
        found.setdefault(run, {})[kind] = json.loads(detail)
    return {run: (sides.get("step.started"), sides.get("step.ended")) for run, sides in found.items()}


def ended_of(store, job_name):
    """The ended detail of the one test_run of the job named `job_name` (by its run_os)."""

    os_name = {LINUX_CHECK: "linux", WINDOWS_CHECK: "windows"}[job_name]
    found = [ended for started, ended in pairs(store).values() if started["run_os"] == os_name]
    assert len(found) == 1
    return found[0]


# --- the pair of a job --------------------------------------------------------------------------

def test_a_green_pull_request_run_gives_each_required_job_a_passed_test_run_pair(store, gh):
    gh.add_run(100)
    report = run_import(store, gh)
    found = pairs(store)
    assert sorted(started["run_os"] for started, _ in found.values()) == ["linux", "windows"]
    assert all(ended["outcome"] == "passed" and ended["reason"] is None for _, ended in found.values())
    assert report.runs_read == 1 and report.jobs == 2


def test_a_test_run_is_named_by_run_job_and_attempt_and_written_by_the_github_role(store, gh):
    gh.add_run(100)
    run_import(store, gh)
    assert set(pairs(store)) == {"github:100/1001/1", "github:100/1002/1"}
    assert store.query("SELECT DISTINCT role, ticket, session, repo FROM events").rows == [
        ("github", None, None, REPO)]


def test_the_started_detail_holds_where_on_what_and_for_which_pull_request(store, gh):
    gh.add_run(100, branch="feat/x", pr=7)
    run_import(store, gh)
    started, _ = pairs(store)["github:100/1001/1"]
    assert started == {"place": "github", "chosen": "github", "reason": None, "run_os": "linux",
                       "commit": "sha100", "branch": "feat/x", "pr": 7, "selection": "full",
                       "event": "pull_request"}


def test_a_run_without_a_pull_request_has_a_null_pr(store, gh):
    gh.add_run(100, event="push", branch="main", pr=None)
    run_import(store, gh)
    started, _ = pairs(store)["github:100/1001/1"]
    assert started["pr"] is None and started["event"] == "push"


@pytest.mark.parametrize("event, selection", [
    ("schedule", "nightly"), ("workflow_dispatch", "nightly"), ("pull_request", "full"), ("push", "full")])
def test_the_selection_is_nightly_for_scheduled_and_dispatched_runs_and_full_otherwise(
        store, gh, event, selection):
    gh.add_run(100, event=event)
    run_import(store, gh)
    started, _ = pairs(store)["github:100/1001/1"]
    assert started["selection"] == selection


def test_the_pair_runs_from_the_start_of_the_job_to_its_completion(store, gh):
    gh.add_run(100)
    run_import(store, gh)
    rows = store.query("SELECT kind, at FROM events WHERE step = 'test_run' AND run = ?"
                       " AND kind IN ('step.started', 'step.ended') ORDER BY at",
                       ("github:100/1001/1",)).rows
    assert rows == [("step.started", "2026-10-26T10:01:00.000000Z"),
                    ("step.ended", "2026-10-26T10:06:00.000000Z")]


def test_a_job_that_never_started_has_its_pair_start_at_its_creation(store, gh):
    gh.add_run(100, linux={"started": False, "conclusion": "cancelled"})
    run_import(store, gh)
    start = store.query("SELECT at FROM events WHERE kind = 'step.started' AND step = 'test_run'"
                        " AND run = ?", ("github:100/1001/1",)).rows
    assert start == [("2026-10-26T10:00:00.000000Z",)]


# --- the minutes ---------------------------------------------------------------------------------

def test_the_ended_detail_gives_start_and_run_minutes_of_the_pytest_step(store, gh):
    gh.add_run(100)
    run_import(store, gh)
    _, ended = pairs(store)["github:100/1001/1"]
    assert ended["start_minutes"] == 1.5 and ended["run_minutes"] == 4.0


def test_billed_minutes_are_the_jobs_billable_duration_rounded_up_to_whole_minutes(store, gh):
    gh.add_run(100, linux={"billed_ms": 270_001}, windows={"billed_ms": 120_000})
    run_import(store, gh)
    assert ended_of(store, LINUX_CHECK)["billed_minutes"] == 5
    assert ended_of(store, WINDOWS_CHECK)["billed_minutes"] == 2


def test_billed_minutes_are_0_when_the_repo_is_not_billed(store, gh):
    gh.add_run(100, billed=False)
    run_import(store, gh)
    assert ended_of(store, LINUX_CHECK)["billed_minutes"] == 0


def test_a_job_that_never_reached_pytest_has_no_run_minutes(store, gh):
    gh.add_run(100, linux={"conclusion": "failure", "cases": None})
    gh.jobs[100][0]["steps"] = gh.jobs[100][0]["steps"][:1]
    run_import(store, gh)
    ended = ended_of(store, LINUX_CHECK)
    assert ended["run_minutes"] is None and ended["start_minutes"] is None


# --- the queue wait ------------------------------------------------------------------------------

def test_the_time_from_created_to_started_is_a_github_queue_wait_on_the_run(store, gh):
    gh.add_run(100)
    run_import(store, gh)
    waits = pairs(store, "wait")
    assert set(waits) == {"github:100/1001/1", "github:100/1002/1"}
    started, ended = waits["github:100/1001/1"]
    assert started == {"reason": "github_queue", "on": "github:100/1001/1"}
    assert ended["outcome"] == "passed"
    rows = store.query("SELECT at FROM events WHERE step = 'wait' AND run = ? ORDER BY at",
                       ("github:100/1001/1",)).rows
    assert rows == [("2026-10-26T10:00:00.000000Z",), ("2026-10-26T10:01:00.000000Z",)]


def test_a_job_that_never_started_has_no_wait(store, gh):
    gh.add_run(100, linux={"started": False, "conclusion": "cancelled"})
    run_import(store, gh)
    assert "github:100/1001/1" not in pairs(store, "wait")
    assert "github:100/1002/1" in pairs(store, "wait")


# --- the test cases ------------------------------------------------------------------------------

def test_each_job_writes_one_test_result_per_case_of_its_report(store, gh):
    gh.add_run(100, windows={"cases": GREEN[:2]})
    run_import(store, gh)
    by_run = dict(store.query("SELECT run, count(*) FROM events WHERE kind = 'test.result'"
                              " GROUP BY run").rows)
    assert by_run == {"github:100/1001/1": 3, "github:100/1002/1": 2}
    assert ended_of(store, LINUX_CHECK)["collected"] == 3 and ended_of(store, WINDOWS_CHECK)["collected"] == 2


def test_a_test_result_row_has_the_cases_id_outcome_and_skip_reason(store, gh):
    gh.add_run(100)
    run_import(store, gh)
    rows = store.query("SELECT step, test_id, outcome, skip_reason FROM events"
                       " WHERE kind = 'test.result' AND run = 'github:100/1001/1' ORDER BY test_id").rows
    assert rows == [("test_run", "tests.test_a::test_one", "passed", None),
                    ("test_run", "tests.test_a::test_two", "passed", None),
                    ("test_run", "tests.test_b::test_three", "skipped", "why")]


def test_the_test_results_are_written_before_the_end_of_the_test_run(store, gh, tmp_path):
    gh.add_run(100, windows={"cases": GREEN[:1]})
    run_import(store, gh)
    kinds = []
    for line in (next((tmp_path / "store").glob("*/*.jsonl"))).read_text("utf-8").splitlines():
        event = json.loads(line)
        if event["run"] == "github:100/1002/1" and event["step"] == "test_run":
            kinds.append(event["kind"])
    assert kinds == ["step.started", "test.result", "step.ended"]


# --- the outcome of a conclusion -----------------------------------------------------------------

def test_a_failed_job_with_a_red_case_is_failed_red(store, gh):
    gh.add_run(100, conclusion="failure", linux={"conclusion": "failure", "cases": RED})
    run_import(store, gh)
    ended = ended_of(store, LINUX_CHECK)
    assert (ended["outcome"], ended["reason"], ended["failed"]) == ("failed", "red", 1)
    assert ended_of(store, WINDOWS_CHECK)["outcome"] == "passed"


def test_a_failed_job_with_no_artifact_is_no_result_summary_missing(store, gh):
    gh.add_run(100, conclusion="failure", linux={"conclusion": "failure", "cases": None})
    report = run_import(store, gh)
    ended = ended_of(store, LINUX_CHECK)
    assert (ended["outcome"], ended["reason"], ended["summary_seen"]) == ("no_result", "summary_missing", False)
    assert report.reports_missing == 1


def test_a_failed_job_whose_report_is_all_green_is_no_result_summary_missing(store, gh):
    gh.add_run(100, conclusion="failure", linux={"conclusion": "failure", "cases": GREEN})
    run_import(store, gh)
    ended = ended_of(store, LINUX_CHECK)
    assert (ended["outcome"], ended["reason"]) == ("no_result", "summary_missing")


def test_a_successful_job_is_passed_whatever_its_report_says(store, gh):
    gh.add_run(100, linux={"cases": RED})
    run_import(store, gh)
    ended = ended_of(store, LINUX_CHECK)
    assert ended["outcome"] == "passed" and ended["failed"] == 1 and ended["collected"] == 2


def test_a_successful_job_with_no_artifact_stays_passed_with_no_rows(store, gh):
    gh.add_run(100, linux={"cases": None})
    report = run_import(store, gh)
    ended = ended_of(store, LINUX_CHECK)
    assert (ended["outcome"], ended["summary_seen"], ended["collected"]) == ("passed", False, 0)
    assert store.query("SELECT count(*) FROM events WHERE run = 'github:100/1001/1'"
                       " AND kind = 'test.result'").rows == [(0,)]
    assert report.reports_missing == 1


def test_a_cancelled_run_with_a_newer_run_of_the_same_branch_is_stopped_superseded(store, gh):
    gh.add_run(100, created=0, conclusion="cancelled", linux={"conclusion": "cancelled", "cases": None},
               windows={"conclusion": "cancelled", "cases": None})
    gh.add_run(101, created=600)
    run_import(store, gh)
    ended = pairs(store)["github:100/1001/1"][1]
    assert (ended["outcome"], ended["reason"]) == ("stopped", "superseded")


def test_a_cancelled_run_with_no_newer_run_on_its_branch_is_stopped_with_no_reason(store, gh):
    gh.add_run(100, created=0, conclusion="cancelled", linux={"conclusion": "cancelled", "cases": None},
               windows={"conclusion": "cancelled", "cases": None})
    gh.add_run(101, created=600, branch="other", pr=9)
    gh.add_run(99, created=-600, branch="feat/x")
    run_import(store, gh)
    ended = pairs(store)["github:100/1001/1"][1]
    assert (ended["outcome"], ended["reason"]) == ("stopped", None)


def test_a_run_created_after_now_does_not_supersede(store, gh):
    gh.add_run(100, created=0, linux={"conclusion": "cancelled", "cases": None})
    gh.add_run(101, created=int((NOW - BASE).total_seconds()) + 3600, status="queued", conclusion=None)
    run_import(store, gh)
    assert pairs(store)["github:100/1001/1"][1]["reason"] is None


def test_a_timed_out_job_is_no_result_cut_even_when_its_partial_report_holds_a_red_case(store, gh):
    gh.add_run(100, linux={"conclusion": "timed_out", "cases": RED})
    run_import(store, gh)
    ended = ended_of(store, LINUX_CHECK)
    assert (ended["outcome"], ended["reason"]) == ("no_result", "cut")
    assert store.query("SELECT count(*) FROM events WHERE outcome = 'failed' AND kind = 'step.ended'"
                       ).rows == [(0,)]


@pytest.mark.parametrize("conclusion", ["neutral", "stale", "startup_failure", "action_required"])
def test_any_other_conclusion_is_no_result_summary_missing(store, gh, conclusion):
    gh.add_run(100, linux={"conclusion": conclusion, "cases": None})
    run_import(store, gh)
    ended = ended_of(store, LINUX_CHECK)
    assert (ended["outcome"], ended["reason"]) == ("no_result", "summary_missing")


def test_a_skipped_job_is_not_a_run_and_writes_nothing(store, gh):
    gh.add_run(100, linux={"conclusion": "skipped", "cases": None})
    run_import(store, gh)
    assert [started["run_os"] for started, _ in pairs(store).values()] == ["windows"]
    assert store.query("SELECT count(*) FROM events WHERE run LIKE 'github:100/1001/%'").rows == [(0,)]


# --- the runs ------------------------------------------------------------------------------------

def test_a_run_holding_only_linux_gets_a_job_missing_pair_for_windows(store, gh):
    gh.add_run(100, jobs=(LINUX_CHECK,))
    run_import(store, gh)
    started, ended = pairs(store)["github:100/windows/1"]
    assert started["run_os"] == "windows" and started["place"] == "github"
    assert (ended["outcome"], ended["reason"], ended["collected"]) == ("no_result", "job_missing", 0)
    assert store.query("SELECT count(*) FROM events WHERE kind = 'test.result' AND"
                       " run = 'github:100/windows/1'").rows == [(0,)]


def test_a_missing_job_pair_runs_from_the_creation_to_the_last_update_of_the_run(store, gh):
    gh.add_run(100, jobs=(LINUX_CHECK,))
    run_import(store, gh)
    rows = store.query("SELECT at FROM events WHERE step = 'test_run' AND run = 'github:100/windows/1'"
                       " ORDER BY at").rows
    assert rows == [("2026-10-26T10:00:00.000000Z",), ("2026-10-26T10:06:40.000000Z",)]


def test_a_job_that_ran_only_in_an_earlier_attempt_is_not_missing_after_a_rerun(store, gh):
    gh.add_run(100, attempt=2, linux={"attempt": 1}, windows={"attempt": 2})
    run_import(store, gh)
    reasons = {run: ended["reason"] for run, (_, ended) in pairs(store).items()}
    assert reasons == {"github:100/1001/1": None, "github:100/1002/2": None}


def test_every_attempt_of_a_run_has_its_own_test_run(store, gh):
    gh.add_run(100, attempt=2, linux={"attempt": 1, "conclusion": "failure", "cases": RED},
               windows={"attempt": 2})
    gh.jobs[100].append(dict(gh.jobs[100][0], id=1003, run_attempt=2, conclusion="success"))
    gh.artifacts[100].append({"name": "junit-linux-2", "expired": False})
    with zipfile.ZipFile(gh.zips / "100-junit-linux-2.zip", "w") as z:
        z.writestr("junit.xml", junit_xml(GREEN))
    run_import(store, gh)
    outcomes = {run: ended["outcome"] for run, (_, ended) in pairs(store).items()}
    assert outcomes == {"github:100/1001/1": "failed", "github:100/1003/2": "passed",
                        "github:100/1002/2": "passed"}


def test_a_run_still_in_progress_is_left_for_the_next_import(store, gh):
    gh.add_run(100, status="in_progress", conclusion=None)
    report = run_import(store, gh)
    assert report.written == 0 and store.query("SELECT count(*) FROM events").rows == [(0,)]
    assert gh.downloads() == []


def test_the_runs_of_every_page_are_read(store, gh):
    for n in range(5):
        gh.add_run(100 + n, created=n * 1000, branch=f"b{n}")
    assert run_import(store, gh).runs_read == 5
    assert len(pairs(store)) == 10


def test_since_leaves_the_runs_created_before_it(store, gh):
    gh.add_run(100, created=0)
    gh.add_run(101, created=7200)
    report = run_import(store, gh, since="2026-10-26T11:00:00Z")
    assert {run.split("/")[0] for run in pairs(store)} == {"github:101"}
    assert report.runs_read == 1


def test_a_since_without_a_zone_is_read_as_utc(store, gh):
    gh.add_run(100, created=0)
    gh.add_run(101, created=7200)
    run_import(store, gh, since="2026-10-26T11:00:00")
    assert {run.split("/")[0] for run in pairs(store)} == {"github:101"}


def test_a_since_that_is_no_time_is_a_coded_error_before_anything_is_asked(store, gh):
    with pytest.raises(GithubError) as error:
        run_import(store, gh, since="yesterday")
    assert error.value.code == "bad_since" and gh.calls == []


def test_the_required_checks_are_the_setting_required_checks(store, gh):
    settings.save("required_checks", [LINUX_CHECK])
    gh.add_run(100)
    run_import(store, gh)
    assert [started["run_os"] for started, _ in pairs(store).values()] == ["linux"]


def test_a_required_job_that_is_no_list_of_names_is_a_bad_setting(store, gh):
    settings.save("required_checks", LINUX_CHECK)
    with pytest.raises(settings.SettingsError) as error:
        run_import(store, gh)
    assert error.value.code == "bad_setting"


# --- the artifacts -------------------------------------------------------------------------------

def test_only_listed_unexpired_artifacts_are_downloaded(store, gh):
    gh.add_run(100, linux={"expired": True}, windows={"cases": None})
    report = run_import(store, gh)
    assert gh.downloads() == []
    assert report.reports_missing == 2


def test_the_artifact_of_the_job_and_attempt_is_downloaded_by_name(store, gh):
    gh.add_run(100)
    run_import(store, gh)
    names = sorted(call[call.index("-n") + 1] for call in gh.downloads())
    assert names == ["junit-linux-1", "junit-windows-1"]
    assert all(call[:5] == ["run", "download", "100", "-R", REPO] for call in gh.downloads())


def test_a_run_from_before_the_attempt_suffix_is_read_from_the_artifact_named_by_the_job(store, gh):
    gh.add_run(100, linux={"artifact_name": "junit-linux"})
    report = run_import(store, gh)
    assert ended_of(store, LINUX_CHECK)["collected"] == 3 and report.reports_missing == 0


def test_an_unreadable_report_is_no_result_summary_missing_for_a_failed_job(store, gh):
    gh.add_run(100, linux={"conclusion": "failure"})
    with zipfile.ZipFile(gh.zips / "100-junit-linux-1.zip", "w") as z:
        z.writestr("junit.xml", "<testsuites><testsuite><testcase")
    run_import(store, gh)
    assert ended_of(store, LINUX_CHECK)["reason"] == "summary_missing"


# --- again, and when gh fails --------------------------------------------------------------------

def test_a_second_import_writes_nothing_and_downloads_nothing(store, gh):
    gh.add_run(100)
    gh.add_run(101, created=3600, jobs=(LINUX_CHECK,))
    first = run_import(store, gh)
    assert first.written > 0
    gh.calls.clear()
    second = run_import(store, gh)
    assert second.written == 0
    assert gh.downloads() == []


def test_a_second_import_leaves_the_event_count_unchanged(store, gh):
    gh.add_run(100)
    run_import(store, gh)
    before = store.query("SELECT count(*) FROM events").rows
    run_import(store, gh)
    assert store.query("SELECT count(*) FROM events").rows == before


def test_an_interrupted_import_is_completed_by_the_next_without_repeating_events(store, gh, tmp_path):
    gh.add_run(100)
    run_import(store, gh)
    total = store.query("SELECT count(*) FROM events").rows[0][0]
    path = next((tmp_path / "store").glob("*/*.jsonl"))
    lines = path.read_text("utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", "utf-8")  # the last event, a step.ended, is lost
    again = run_import(store, gh)
    assert again.written == 1
    assert store.query("SELECT count(*) FROM events").rows == [(total,)]


def test_gh_failing_on_a_run_skips_that_run_and_imports_the_later_ones(store, gh):
    gh.add_run(100, created=0)
    gh.add_run(101, created=3600)
    gh.add_run(102, created=7200)
    gh.fail_when = lambda args: args[0] == "run" and args[2] == "101"
    report = run_import(store, gh)
    assert (report.runs_failed, report.failure_codes) == (1, ["gh_failed"])
    assert {run.split("/")[0] for run in pairs(store)} == {"github:100", "github:102"}
    assert store.query("SELECT count(*) FROM events WHERE run LIKE 'github:101/%'").rows == [(0,)]


def test_a_run_whose_jobs_call_fails_leaves_nothing_and_does_not_count_its_jobs(store, gh):
    gh.add_run(100)
    gh.fail_when = lambda args: "/jobs" in args[-1]
    report = run_import(store, gh)
    assert (report.runs_failed, report.jobs, report.written) == (1, 0, 0)
    assert store.query("SELECT count(*) FROM events").rows == [(0,)]


@pytest.mark.parametrize("call", ["/artifacts", "/timing"])
def test_a_run_whose_artifacts_or_timing_call_fails_is_skipped_and_retried_next_time(store, gh, call):
    gh.add_run(100)
    gh.fail_when = lambda args: args[0] == "api" and args[-1].partition("?")[0].endswith(call)
    assert run_import(store, gh).runs_failed == 1
    gh.fail_when = None
    assert run_import(store, gh).runs_failed == 0 and len(pairs(store)) == 2


def test_a_timeout_on_one_run_is_counted_under_its_code(store, gh):
    gh.add_run(100)

    def timing_out(args):
        if args[0] == "run":
            raise GithubError("gh_timeout", "slow")
        return gh(args)
    report = import_github(store, REPO, gh=timing_out, now=NOW)
    assert (report.runs_failed, report.failure_codes) == (1, ["gh_timeout"])


def test_the_import_after_a_failure_finishes_the_run_that_failed(store, gh):
    gh.add_run(100)
    gh.fail_when = lambda args: args[0] == "run"
    assert run_import(store, gh).runs_failed == 1 and pairs(store) == {}
    gh.fail_when = None
    report = run_import(store, gh)
    assert report.written > 0 and len(pairs(store)) == 2


def test_gh_failing_to_list_the_runs_is_a_coded_error(store, gh):
    gh.fail_when = lambda args: True
    with pytest.raises(GithubError) as error:
        run_import(store, gh)
    assert error.value.code == "gh_failed"


def test_gh_answering_with_no_json_is_a_coded_error(store):
    with pytest.raises(GithubError) as error:
        import_github(store, REPO, gh=lambda args: "not json", now=NOW)
    assert error.value.code == "gh_bad_output"


class Completed:
    def __init__(self, returncode=0, stdout=b"{}", stderr=b""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_the_default_runner_turns_a_timeout_into_gh_timeout(monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(GithubError) as error:
        github.run_gh(["api", "x"])
    assert error.value.code == "gh_timeout"


def test_the_default_runner_turns_a_failed_exit_into_gh_failed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Completed(1, b"", b"HTTP 404"))
    with pytest.raises(GithubError) as error:
        github.run_gh(["api", "x"])
    assert error.value.code == "gh_failed"


def test_the_default_runner_is_bounded_and_hides_the_console_window(monkeypatch):
    seen = {}

    def fake(command, **kwargs):
        seen.update(kwargs, command=command)
        return Completed(0, b'{"ok": true}')
    monkeypatch.setattr(subprocess, "run", fake)
    assert github.run_gh(["api", "x"]) == '{"ok": true}'
    assert seen["command"] == ["gh", "api", "x"]
    assert seen["timeout"] > 0
    assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_a_test_that_overrides_nothing_never_reaches_the_real_gh():
    with pytest.raises(GithubError) as error:
        github.run_gh(["api", "x"])
    assert error.value.code == "gh_failed"
    assert "no real gh in a test" in str(error.value)


def test_the_default_runner_turns_a_missing_gh_into_gh_failed(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(GithubError) as error:
        github.run_gh(["api", "x"])
    assert error.value.code == "gh_failed"


# --- reruns, cancellations and the bound --------------------------------------------------------

def test_a_run_whose_latest_attempt_is_stored_is_skipped_before_any_jobs_call(store, gh):
    gh.add_run(100)
    run_import(store, gh)
    gh.calls.clear()
    report = run_import(store, gh)
    assert len(gh.calls) == 1 and gh.calls[0][-1].partition("?")[0].endswith("/runs")
    assert report.written == 0 and report.jobs == 2


def test_a_run_with_a_skipped_job_is_read_again_but_writes_and_downloads_nothing(store, gh):
    gh.add_run(100, linux={"conclusion": "skipped", "cases": None})
    run_import(store, gh)
    gh.calls.clear()
    assert run_import(store, gh).written == 0 and gh.downloads() == []


def test_a_cancelled_run_lacking_a_required_job_gets_stopped_pairs_not_job_missing(store, gh):
    gh.add_run(100, jobs=(), conclusion="cancelled")
    gh.add_run(101, created=600)
    run_import(store, gh)
    for name in (LINUX_CHECK, WINDOWS_CHECK):
        _, ended = pairs(store)[f"github:100/{name}/1"]
        assert (ended["outcome"], ended["reason"]) == ("stopped", "superseded")


def test_a_cancelled_run_lacking_a_job_and_with_no_newer_run_is_stopped_with_no_reason(store, gh):
    gh.add_run(100, jobs=(LINUX_CHECK,), conclusion="cancelled")
    run_import(store, gh)
    _, ended = pairs(store)["github:100/windows/1"]
    assert (ended["outcome"], ended["reason"]) == ("stopped", None)


def cancelled_linux(**over):
    return dict(created=0, conclusion="cancelled", jobs=(LINUX_CHECK,),
                linux={"conclusion": "cancelled", "cases": None}) | over


def test_a_manual_cancel_with_only_a_later_dispatched_run_is_stopped_with_no_reason(store, gh):
    gh.add_run(100, **cancelled_linux())
    gh.add_run(101, created=600, event="workflow_dispatch", branch="feat/x", pr=None)
    run_import(store, gh)
    assert pairs(store)["github:100/1001/1"][1]["reason"] is None


def test_a_pull_request_run_is_superseded_only_by_a_newer_run_of_the_same_pull_request(store, gh):
    gh.add_run(100, **cancelled_linux(pr=7, branch="main"))  # a fork's branch is often "main"
    gh.add_run(101, created=600, pr=8, branch="main")
    run_import(store, gh)
    assert pairs(store)["github:100/1001/1"][1]["reason"] is None


def test_a_newer_run_with_the_same_head_commit_does_not_supersede(store, gh):
    gh.add_run(100, **cancelled_linux(sha="same"))
    gh.add_run(101, created=600, sha="same")
    run_import(store, gh)
    assert pairs(store)["github:100/1001/1"][1]["reason"] is None


def test_a_push_run_is_superseded_by_a_newer_push_run_of_the_same_branch(store, gh):
    gh.add_run(100, **cancelled_linux(event="push", pr=None, branch="main"))
    gh.add_run(101, created=600, event="push", pr=None, branch="main")
    run_import(store, gh)
    assert pairs(store)["github:100/1001/1"][1]["reason"] == "superseded"


def test_a_cancelled_job_that_ran_to_the_bound_is_no_result_cut(store, gh):
    gh.add_run(100, linux={"conclusion": "cancelled", "cases": RED, "completed": 60 + github.DEFAULT_JOB_TIMEOUT_MINUTES * 60})
    run_import(store, gh)
    ended = ended_of(store, LINUX_CHECK)
    assert (ended["outcome"], ended["reason"]) == ("no_result", "cut")


def test_a_cancelled_job_that_stopped_short_of_the_bound_is_stopped(store, gh):
    gh.add_run(100, linux={"conclusion": "cancelled", "cases": None, "completed": 60 + github.DEFAULT_JOB_TIMEOUT_MINUTES * 60 - 1})
    run_import(store, gh)
    assert ended_of(store, LINUX_CHECK)["outcome"] == "stopped"


def test_the_bound_is_the_setting_github_job_timeout_minutes(store, gh):
    settings.save("github_job_timeout_minutes", 3)
    gh.add_run(100, linux={"conclusion": "cancelled", "cases": None, "completed": 60 + 3 * 60})
    run_import(store, gh)
    assert ended_of(store, LINUX_CHECK)["reason"] == "cut"


WORKFLOW = (Path(__file__).parents[1] / ".github" / "workflows" / "tests.yml").read_text("utf-8")


def test_the_workflow_bounds_are_read_per_job_with_the_nightly_value_beside_the_per_change_one():
    assert github.workflow_bounds(WORKFLOW) == {
        LINUX_CHECK: (4, 16), WINDOWS_CHECK: (5, 16), FOREIGN_LINUX: (9, 11), FOREIGN_WINDOWS: (12, 40),
        FOREIGN_MACOS: (30, 30)}


@pytest.mark.parametrize("job, minutes", [(LINUX_CHECK, 4), (WINDOWS_CHECK, 5)])
def test_a_job_cancelled_at_its_workflow_bound_is_read_as_cut(store, gh, job, minutes):
    gh.add_run(100, **{job: {"conclusion": "cancelled", "cases": None, "completed": 60 + minutes * 60}})
    run_import(store, gh, workflow_text=WORKFLOW)
    assert ended_of(store, job)["reason"] == "cut"


def test_a_job_cancelled_by_a_person_at_2_minutes_is_not_cut(store, gh):
    gh.add_run(100, linux={"conclusion": "cancelled", "cases": None, "completed": 60 + 2 * 60})
    run_import(store, gh, workflow_text=WORKFLOW)
    assert ended_of(store, LINUX_CHECK)["outcome"] == "stopped"


def test_a_nightly_run_reads_the_nightly_bound_of_its_job(store, gh):
    gh.add_run(100, event="schedule", pr=None, branch=DEFAULT_BRANCH,
               linux={"conclusion": "cancelled", "cases": None, "completed": 60 + 5 * 60})
    run_import(store, gh, workflow_text=WORKFLOW)
    assert ended_of(store, LINUX_CHECK)["outcome"] == "stopped"


def test_a_dispatch_run_without_the_slow_input_reads_the_per_change_bound_so_4_minutes_is_a_cut(store, gh):
    # the run listing does not show the `slow_and_live` input: a dispatch reads the smaller of the job's two bounds
    gh.add_run(100, event="workflow_dispatch", pr=None, branch=DEFAULT_BRANCH,
               linux={"conclusion": "cancelled", "cases": None, "completed": 60 + 4 * 60})
    run_import(store, gh, workflow_text=WORKFLOW)
    assert (ended_of(store, LINUX_CHECK)["outcome"], ended_of(store, LINUX_CHECK)["reason"]) == ("no_result", "cut")


def test_a_cancelled_job_that_never_started_is_never_cut(store, gh):
    gh.add_run(100, linux={"conclusion": "cancelled", "cases": None, "started": False,
                           "completed": 99999})
    run_import(store, gh)
    assert ended_of(store, LINUX_CHECK)["outcome"] == "stopped"


@pytest.mark.parametrize("value", [0, -1, "15", True, None])
def test_a_job_timeout_that_is_no_positive_number_is_a_bad_setting(store, gh, value):
    settings.save("github_job_timeout_minutes", value)
    with pytest.raises(settings.SettingsError) as error:
        run_import(store, gh)
    assert error.value.code == "bad_setting"


@pytest.mark.parametrize("names", [[" linux"], ["linux "], [""], ["  "], []])
def test_required_job_names_with_blanks_or_empty_are_a_bad_setting(store, gh, names):
    settings.save("required_checks", names)
    with pytest.raises(settings.SettingsError) as error:
        run_import(store, gh)
    assert error.value.code == "bad_setting"


def test_a_job_that_started_before_it_was_created_has_a_wait_of_0(store, gh):
    gh.add_run(100, linux={"started": -30})
    run_import(store, gh)
    rows = store.query("SELECT kind, at FROM events WHERE step = 'wait' AND run = ? ORDER BY at, kind",
                       ("github:100/1001/1",)).rows
    assert rows == [("step.ended", "2026-10-26T10:00:00.000000Z"),
                    ("step.started", "2026-10-26T10:00:00.000000Z")]


# --- what gh may answer ---------------------------------------------------------------------------

def test_a_run_with_no_id_in_the_list_is_a_bad_output(store, gh):
    gh.add_run(100)
    gh.runs.append({"status": "completed", "created_at": stamp(BASE, 0)})
    with pytest.raises(GithubError) as error:
        run_import(store, gh)
    assert error.value.code == "gh_bad_output"


def test_a_run_that_is_no_object_in_the_list_is_a_bad_output(store, gh):
    gh.add_run(100)
    gh.runs.append("run")
    with pytest.raises(GithubError) as error:
        run_import(store, gh)
    assert error.value.code == "gh_bad_output"


def test_a_job_with_no_id_skips_its_run_as_a_bad_output(store, gh):
    gh.add_run(100)
    del gh.jobs[100][0]["id"]
    report = run_import(store, gh)
    assert (report.runs_failed, report.failure_codes, report.written) == (1, ["gh_bad_output"], 0)


@pytest.mark.parametrize("steps", ["pytest", ["pytest"], [None]])
def test_steps_that_are_no_objects_skip_the_run_as_a_bad_output(store, gh, steps):
    gh.add_run(100)
    gh.jobs[100][0]["steps"] = steps
    report = run_import(store, gh)
    assert (report.runs_failed, report.failure_codes) == (1, ["gh_bad_output"])


def test_a_timing_answer_of_another_shape_skips_the_run_as_a_bad_output(store, gh):
    gh.add_run(100)
    gh.timing[100] = {"billable": {"UBUNTU": {"job_runs": ["x"]}}}
    report = run_import(store, gh)
    assert (report.runs_failed, report.failure_codes) == (1, ["gh_bad_output"])


@pytest.mark.parametrize("repo", ["orch-it", "a/b/c", "a b/c", "", "a/..", "/b"])
def test_a_repo_that_is_no_owner_slash_name_is_refused_before_gh_is_called(store, gh, repo):
    with pytest.raises(GithubError) as error:
        import_github(store, repo, gh=gh, now=NOW)
    assert error.value.code == "bad_repo" and gh.calls == []


def test_the_command_refuses_a_bad_repo_with_error_bad_repo(tmp_path, gh, monkeypatch, capsys):
    monkeypatch.setattr(github, "run_gh", gh)
    assert main(["--store", str(tmp_path / "s"), "events", "import-github", "--repo", "nope"]) == 2
    assert capsys.readouterr().err.startswith("error: bad_repo")


def test_the_command_prints_the_failed_runs_and_their_codes(tmp_path, gh, monkeypatch, capsys):
    gh.add_run(100)
    gh.fail_when = lambda args: args[0] == "run"
    monkeypatch.setattr(github, "run_gh", gh)
    assert main(["--store", str(tmp_path / "s"), "events", "import-github", "--repo", REPO]) == 0
    printed = capsys.readouterr().out
    assert "runs_failed=1" in printed and "failure_codes=gh_failed" in printed


def test_the_runs_list_call_may_take_longer_than_the_other_calls(monkeypatch):
    timeouts = {}

    def fake(command, **kwargs):
        timeouts[command[-1]] = kwargs["timeout"]
        return Completed(0, b"{}")
    monkeypatch.setattr(subprocess, "run", fake)
    github.run_gh(["api", "--paginate", "repos/a/b/actions/workflows/tests.yml/runs?per_page=100"])
    github.run_gh(["api", "repos/a/b/actions/runs/1/timing"])
    assert timeouts["repos/a/b/actions/workflows/tests.yml/runs?per_page=100"] == 600
    assert timeouts["repos/a/b/actions/runs/1/timing"] < 600


# --- the command ---------------------------------------------------------------------------------

def test_the_command_prints_the_counts_of_the_import(tmp_path, gh, monkeypatch, capsys):
    gh.add_run(100)
    monkeypatch.setattr(github, "run_gh", gh)
    assert main(["--store", str(tmp_path / "cli-store"), "events", "import-github",
                 "--repo", REPO]) == 0
    printed = capsys.readouterr().out
    assert "runs_read=1" in printed and "jobs=2" in printed and "reports_missing=0" in printed
    assert "written=" in printed and "already_stored=0" in printed
    assert "repos/acme-x/widgets-y/" in " ".join(gh.calls[0])


def test_the_command_takes_the_repo_and_since(tmp_path, gh, monkeypatch, capsys):
    gh.add_run(100, created=0)
    gh.add_run(101, created=7200)
    monkeypatch.setattr(github, "run_gh", gh)
    assert main(["--store", str(tmp_path / "s"), "events", "import-github", "--repo", "a/b",
                 "--since", "2026-10-26T11:00:00Z"]) == 0
    assert "runs_read=1" in capsys.readouterr().out
    assert "repos/a/b/" in " ".join(gh.calls[0])


def test_the_command_turns_a_gh_failure_into_error_gh_failed_and_exit_2(tmp_path, gh, monkeypatch, capsys):
    gh.fail_when = lambda args: True
    monkeypatch.setattr(github, "run_gh", gh)
    assert main(["--store", str(tmp_path / "s"), "events", "import-github", "--repo", REPO]) == 2
    assert capsys.readouterr().err.startswith("error: gh_failed")



# --- the real runs -------------------------------------------------------------------------------

COUNTED_IN_SUMMARY = ("passed", "failed", "skipped", "error", "errors", "xfailed", "xpassed")
SUMMARY_LINE = re.compile(r"((?:\d+ [a-z]+(?:, )?)+) in \d+(?:\.\d+)?s")


def summary_count(log: str) -> int | None:
    """The tests a pytest log says it ran: the sum of the counts in every summary line of the log
    (`==== 312 passed, 1 skipped, 1 deselected in 82.07s ====`), deselected ones left out. A job that runs
    pytest in two phases (the parallel one, then the `serial` one) prints one summary line per phase,
    and the report holds the tests of both."""

    total, seen = 0, False
    for line in log.splitlines():
        columns = line.split("\t", 2)
        if len(columns) == 3 and columns[1] != github.TEST_STEP:
            continue  # only the test step's lines: a pull request body printed by another step says "143 passed in 2.03s" too
        found = SUMMARY_LINE.search(line)
        if found:
            seen = True
            total += sum(int(n) for n, word in re.findall(r"(\d+) ([a-z]+)", found.group(1)) if word in COUNTED_IN_SUMMARY)
    return total if seen else None


def test_the_summary_count_sums_the_summary_lines_of_both_phases_of_a_job():
    log = "linux\tpytest\t3156 passed, 3 skipped in 95.25s (0:01:35)\nlinux\tpytest\t3159 deselected in 0.40s\n"
    assert summary_count(log) == 3159
    assert summary_count("1 failed, 3 passed in 5.00s\n1 passed in 1.00s\nno tests ran in 0.10s\n") == 5


def test_the_summary_count_reads_only_the_pytest_step_of_a_log():
    log = ("linux\tclosing words\t2026-10-29T17:49:41Z Tests: tests/test_x.py: 143 passed in 2.03s on the branch\n"
           "linux\tpytest\t2026-10-29T17:51:47Z 3271 passed, 3 skipped in 117.11s (0:01:57)\n")
    assert summary_count(log) == 3274


def test_the_summary_count_sums_what_ran_and_leaves_deselected_out():
    assert summary_count("==== 312 passed, 1 skipped, 1 deselected in 82.07s ====") == 313
    assert summary_count("noise\n312 passed, 1 deselected in 82.07s\n") == 312
    assert summary_count("linux\tpytest\t2026-10-25T20:50:01.1Z 1 failed, 2 passed, 1 error, 3 warnings"
                         " in 3.00s (0:00:03)") == 4
    assert summary_count("no summary here") is None


# the margin the live test leaves for skew between the runner's clock and GitHub's
CLOCK_SKEW_MARGIN = timedelta(seconds=60)

MAPPED = {"success": {"passed"}, "failure": {"failed", "no_result"}, "cancelled": {"stopped", "no_result"}}


def completed_before(runs, moment, since):
    """The completed runs created at or after `since` and completed strictly before `moment`
    (both `%Y-%m-%dT%H:%M:%SZ`, UTC), newest first. A run's `updated_at` is its completion."""

    since_at, moment_at = datetime.fromisoformat(since), datetime.fromisoformat(moment)
    judged = [r for r in runs if r["status"] == "completed"
              and datetime.fromisoformat(r["created_at"]) >= since_at
              and datetime.fromisoformat(r["updated_at"]) < moment_at]
    return sorted(judged, key=lambda r: datetime.fromisoformat(r["created_at"]), reverse=True)


# Runs that finish during the live test fall after the moment and are left out of the judged
# runs; the test lists this many more than it judges, and the slack replaces them.
LIVE_SLACK = 15


def newest_judged(runs, moment, since, count):
    """The newest `count` of the completed runs `completed_before` keeps."""

    return completed_before(runs, moment, since)[:count]


def require_enough_judged(judged, listed_runs, total_count, count):
    """Fails when fewer than `count` runs are judged although the history held more runs than the
    list showed; a history the list showed whole (`total_count` not above its length) may give
    fewer, whatever completed after the moment."""

    if len(judged) < count and total_count > len(listed_runs):
        pytest.fail(f"only {len(judged)} of the {len(listed_runs)} listed completed runs ({total_count} in the"
                    f" history) completed before the moment, {count} wanted")


def rows_per_run(store):
    """run id -> how many events the store holds under the keys `github:<run id>/...`."""

    counts = {}
    for run, rows in store.query(
            "SELECT run, count(*) FROM events WHERE run LIKE 'github:%' GROUP BY run").rows:
        run_id = int(run.split("/")[0].removeprefix("github:"))
        counts[run_id] = counts.get(run_id, 0) + rows
    return counts


def test_the_slack_keeps_n_runs_judged_when_runs_complete_after_the_moment():
    moment, since = "2026-10-29T13:00:00Z", "2026-10-25T20:40:00Z"
    runs = [{"id": n, "status": "completed", "created_at": f"2026-10-29T12:{59 - n:02d}:00Z",
             "updated_at": "2026-10-29T13:05:00Z" if n < 3 else "2026-10-29T12:50:00Z"}
            for n in range(4 + LIVE_SLACK)]  # the 3 newest completed after the moment
    assert [r["id"] for r in newest_judged(runs, moment, since, 4)] == [3, 4, 5, 6]
    assert len(newest_judged(runs[:5], moment, since, 4)) == 2  # no slack: the cut leaves fewer


def test_the_slack_holds_when_a_busy_window_completes_six_runs_after_the_moment():
    """#448: eight runs dispatched inside nine minutes left 6 completing after the moment, one more than the
    old slack of 5; N runs are still judged while the completions after the moment stay within the slack."""

    moment, since = "2026-10-29T13:00:00Z", "2026-10-25T20:40:00Z"
    after = 6
    runs = [{"id": n, "status": "completed", "created_at": f"2026-10-29T12:{59 - n:02d}:00Z",
             "updated_at": "2026-10-29T13:05:00Z" if n < after else "2026-10-29T12:50:00Z"}
            for n in range(20 + LIVE_SLACK)]
    assert len(newest_judged(runs, moment, since, 20)) == 20
    assert [r["id"] for r in newest_judged(runs, moment, since, 20)][:2] == [after, after + 1]


def test_completed_before_keeps_the_runs_completed_before_the_moment_since_the_floor():
    moment, since = "2026-10-29T13:00:00Z", "2026-10-25T20:40:00Z"

    def run(run_id, created, updated, status="completed"):
        return {"id": run_id, "status": status, "created_at": created, "updated_at": updated}

    runs = [
        run(1, "2026-10-29T12:00:00Z", "2026-10-29T12:30:00Z"),
        run(2, "2026-10-29T12:10:00Z", "2026-10-29T13:00:01Z"),
        run(3, "2026-10-29T12:20:00Z", "2026-10-29T13:00:00Z"),
        run(4, "2026-10-29T12:30:00Z", "2026-10-29T12:40:00Z", status="in_progress"),
        run(5, "2026-10-25T20:30:00Z", "2026-10-25T21:00:00Z"),
        run(6, "2026-10-28T09:00:00Z", "2026-10-28T10:00:00Z"),
    ]
    assert [r["id"] for r in completed_before(runs, moment, since)] == [1, 6]


@pytest.mark.live
def test_every_completed_tests_run_since_the_first_pull_request_run_is_stored_as_mapped(tmp_path, capsys, record_property):
    """Runs the real `gh` against the repo (nightly only, where GH_TOKEN is set), on the newest N
    completed runs (setting `github_live_newest_runs`, default 20; N + LIVE_SLACK are listed) so that its `gh` calls are the
    same number whatever the history holds: each required
    job of each run completed before a moment 60 s before the first import has a pair with the
    outcome its conclusion maps to and a queue wait when it started; the test rows of the newest
    3 pull request runs among them equal the counts of their pytest logs; and a second import
    writes no row of those runs (a run completed later is not judged)."""

    if shutil.which("gh") is None:
        pytest.skip("no gh")
    auth = subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=60,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if auth.returncode != 0:
        pytest.skip("gh is not signed in")
    # the checkout's own GitHub origin: the live test reads the real repo, never an invented one
    live_repo = ticket_hook.repo_of(str(Path(__file__).resolve().parent.parent))
    if not live_repo:
        pytest.skip("no GitHub origin in this checkout")
    since = "2026-10-25T20:40:00Z"
    # the runner's clock minus 60 s of margin for skew with GitHub: a run completed after it is not judged
    moment = (datetime.now(UTC) - CLOCK_SKEW_MARGIN).strftime("%Y-%m-%dT%H:%M:%SZ")
    started_at = time.monotonic()
    newest = github.newest_runs()
    listed = newest + LIVE_SLACK
    assert listed <= github.MAX_PER_PAGE, (
        f"the setting {github.NEWEST_RUNS_SETTING} ({newest}) plus the slack ({LIVE_SLACK}) is above"
        f" {github.MAX_PER_PAGE}, GitHub's per-page maximum")
    calls = []

    def counted_gh(args):
        calls.append(args)
        return github.run_gh(args)

    store = Store.open(tmp_path / "live-store")
    first_import = import_github(store, live_repo, gh=counted_gh, since=since, newest=listed)
    listing = json.loads(counted_gh(github._list_runs_args(live_repo, github._since(since), listed)))
    runs, completed_since = listing["workflow_runs"], listing["total_count"]
    judged = newest_judged(runs, moment, since, newest)
    require_enough_judged(judged, runs, completed_since, newest)
    assert judged, "no run completed since the first pull request run and before the moment"
    required = (LINUX_CHECK, WINDOWS_CHECK)
    run_pairs, waits = pairs(store), pairs(store, "wait")
    jobs_of = {}
    for run in judged:
        jobs = github._items(counted_gh(
            ["api", "--paginate", f"repos/{live_repo}/actions/runs/{run['id']}/jobs?filter=all&per_page=100"]),
            "jobs")
        jobs_of[run["id"]] = [j for j in jobs if j["name"] in required and j["conclusion"] != "skipped"]
        for job in jobs_of[run["id"]]:
            key = f"github:{run['id']}/{job['id']}/{job['run_attempt']}"
            assert key in run_pairs, (f"{key}: first import runs_failed={first_import.runs_failed}, "
                                      f"failure_codes={first_import.failure_codes}")
            assert run_pairs[key][1]["outcome"] in MAPPED.get(job["conclusion"], {"no_result"}), key
            assert (key in waits) == bool(job["started_at"]), key
        seen = {started["run_os"] for key, (started, _) in run_pairs.items()
                if key.startswith(f"github:{run['id']}/")}
        assert seen >= set(required), run["id"]

    compared = 0
    for run in [r for r in judged if r["event"] == "pull_request"][:3]:
        for job in jobs_of[run["id"]]:
            key = f"github:{run['id']}/{job['id']}/{job['run_attempt']}"
            if job["conclusion"] not in ("success", "failure") or not run_pairs[key][1]["summary_seen"]:
                continue
            log = counted_gh(["run", "view", str(run["id"]), "-R", live_repo, "--log", "--job", str(job["id"])])
            rows = store.query("SELECT count(*) FROM events WHERE kind = 'test.result' AND run = ?",
                               (key,)).rows[0][0]
            assert rows == summary_count(log), key
            compared += 1
    assert compared, "no pull request job with a report to count"
    judged_ids = [r["id"] for r in judged]
    before = rows_per_run(store)
    import_github(store, live_repo, gh=counted_gh, since=since, newest=listed)
    after = rows_per_run(store)
    changed = {i: (before.get(i, 0), after.get(i, 0)) for i in judged_ids
               if before.get(i, 0) != after.get(i, 0)}
    assert not changed, f"a second import wrote rows of judged runs (run id: before, after): {changed}"
    line = (f"live import test: duration {time.monotonic() - started_at:.0f} s;"
            f" runs judged {len(judged)}; setting {newest}; slack {LIVE_SLACK};"
            f" completed tests runs since {since}: {completed_since};"
            f" gh API calls: {len(calls)}")
    record_property("live_import_line", line)  # a parallel worker's terminal output is lost; the JUnit report keeps the line
    with capsys.disabled():
        print("\n" + line)


def runs_list_calls(gh):
    return [c for c in gh.calls if c[-1].partition("?")[0].endswith(f"/workflows/{github.WORKFLOW_FILE}/runs")]


NEWEST = github.DEFAULT_NEWEST_RUNS
SHORT_HISTORY, LONG_HISTORY = 30, 300
FIRST_ID = 8000  # the id of the newest run of `add_history`
RUN_SPACING_S = 1000
CALLS_PER_RUN = 5  # jobs, artifacts and timing of the run, one download per required job


def add_history(gh, count):
    """`count` completed runs, one every `RUN_SPACING_S`, run n created (count - n) * RUN_SPACING_S
    after BASE and given id FIRST_ID + n: the ids run against the creation order (the newest run
    has the smallest id)."""

    for n in range(count):
        gh.add_run(FIRST_ID + n, created=(count - n) * RUN_SPACING_S, branch=f"b{n}", pr=n + 1)


def test_newest_asks_for_one_page_of_the_newest_completed_runs_and_not_for_every_page(store, gh):
    add_history(gh, SHORT_HISTORY)
    run_import(store, gh, newest=NEWEST)
    (listing,) = runs_list_calls(gh)
    assert "--paginate" not in listing
    assert f"per_page={NEWEST}" in listing[-1] and "status=completed" in listing[-1]


@pytest.mark.parametrize("history", [SHORT_HISTORY, LONG_HISTORY])
def test_newest_makes_the_same_number_of_gh_calls_whatever_the_history(store, gh, history):
    add_history(gh, history)
    run_import(store, gh, newest=NEWEST)
    assert len(gh.calls) == 1 + NEWEST * CALLS_PER_RUN  # one list of runs, then each run's calls


def test_newest_imports_the_newest_runs_by_creation_and_no_older_one(store, gh):
    add_history(gh, LONG_HISTORY)
    report = run_import(store, gh, newest=NEWEST)
    imported = {int(run.split("/")[0].removeprefix("github:")) for run in pairs(store)}
    assert imported == set(range(FIRST_ID, FIRST_ID + NEWEST))
    assert report.runs_read == NEWEST and report.jobs == 2 * NEWEST


def test_newest_larger_than_the_history_imports_every_run(store, gh):
    short = NEWEST // 4
    add_history(gh, short)
    assert run_import(store, gh, newest=NEWEST).runs_read == short
    assert len(pairs(store)) == 2 * short


def test_newest_counts_completed_runs_only(store, gh):
    gh.add_run(100, created=0)
    gh.add_run(101, created=RUN_SPACING_S, status="in_progress", conclusion=None)
    gh.add_run(102, created=2 * RUN_SPACING_S)
    assert run_import(store, gh, newest=2).runs_read == 2
    assert {run.split("/")[0] for run in pairs(store)} == {"github:100", "github:102"}


def test_newest_with_since_asks_only_for_the_runs_created_since_it(store, gh):
    add_history(gh, SHORT_HISTORY)
    report = run_import(store, gh, newest=NEWEST, since="2026-10-26T15:00:00Z")
    (listing,) = runs_list_calls(gh)
    assert "created=>=2026-10-26T15:00:00Z" in listing[-1]
    assert report.runs_read == 13  # created at 15:00 or later, 5 h after BASE: SHORT_HISTORY - n >= 18


@pytest.mark.parametrize("bad", [0, -1, True, 2.5, "20", github.MAX_PER_PAGE + 1])
def test_a_newest_that_is_no_whole_number_from_1_to_the_page_maximum_is_a_bad_setting(store, gh, bad):
    with pytest.raises(settings.SettingsError, match=f"1 to {github.MAX_PER_PAGE}") as raised:
        run_import(store, gh, newest=bad)
    assert raised.value.code == "bad_setting" and gh.calls == []


def test_the_page_maximum_itself_is_a_good_newest(store, gh):
    add_history(gh, 2)
    assert run_import(store, gh, newest=github.MAX_PER_PAGE).runs_read == 2


def test_the_newest_runs_setting_defaults_to_20_and_is_read_from_the_settings_file(tmp_path):
    assert github.newest_runs() == NEWEST == 20
    (tmp_path / "settings.json").write_text(json.dumps({"github_live_newest_runs": 7}), encoding="utf-8")
    assert github.newest_runs() == 7


@pytest.mark.parametrize("bad", [0, -3, True, "20", 2.5, github.MAX_PER_PAGE + 1])
def test_a_stored_newest_runs_setting_that_is_no_whole_number_from_1_to_the_page_maximum_is_bad(tmp_path, bad):
    (tmp_path / "settings.json").write_text(json.dumps({"github_live_newest_runs": bad}), encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="github_live_newest_runs") as raised:
        github.newest_runs()
    assert raised.value.code == "bad_setting"


def test_the_live_selection_fails_only_when_the_list_hid_runs_that_could_have_been_judged():
    listed = [{}] * (NEWEST + LIVE_SLACK)
    # a history the list showed whole (NEWEST + 2 runs, one completed after the moment): fewer is fine
    require_enough_judged([{}] * NEWEST, [{}] * (NEWEST + 2), NEWEST + 2, NEWEST)
    require_enough_judged([{}] * (NEWEST - 1), [{}] * NEWEST, NEWEST, NEWEST)
    # a longer history, and the list too short before the moment: fails with the counts
    with pytest.raises(pytest.fail.Exception, match=rf"only {NEWEST - 3} of the {len(listed)} listed"):
        require_enough_judged([{}] * (NEWEST - 3), listed, 3 * NEWEST, NEWEST)


def test_json_values_is_public_and_reads_pages_printed_one_after_the_other():
    assert github.json_values('[1] [2]\n{"a": 3}') == [[1], [2], {"a": 3}]
    assert github.json_values("") == []
    assert not hasattr(github, "_json_values")


# --- the Actions budget reading stored with a run (#414 unit 12a) ------------------------------------------------

def rate_limit_text(remaining, reset_at, limit=1000):
    core = {"limit": limit, "used": limit - remaining, "remaining": remaining, "reset": int(reset_at.timestamp())}
    return json.dumps({"resources": {"core": core, "search": {"limit": 30, "remaining": 30, "reset": 0}}})


def readings(store):
    return store.query(
        "SELECT repo, budget_remaining, budget_limit, budget_reset, at FROM events WHERE kind = 'github.called'"
        " AND budget = 'actions' ORDER BY at").rows


def test_a_run_with_the_rate_limit_file_stores_one_reading_and_one_without_stores_none(store, gh):
    reset = BASE + timedelta(minutes=50)
    both = rate_limit_text(940, reset)
    gh.add_run(100, **{LINUX_CHECK: {"rate_limit": both}, WINDOWS_CHECK: {"rate_limit": both}})  # a file in each job
    gh.add_run(101, created=60)  # no file
    report = run_import(store, gh)
    assert report.runs_failed == 0
    found = readings(store)
    assert len(found) == 1  # one reading per run, not one per job
    repo, remaining, limit, stored_reset, at = found[0]
    assert (repo, remaining, limit) == (REPO, 940, 1000)
    assert stored_reset.startswith(reset.strftime("%Y-%m-%dT%H:%M:%S"))
    assert run_import(store, gh).written == 0 and len(readings(store)) == 1  # a second import adds none


@pytest.mark.parametrize("text", ["not json {", "[]", json.dumps({"resources": {"core": {"limit": 1000}}}),
                                  json.dumps({"resources": {"core": {"limit": 0, "remaining": 5}}}), ""])
def test_an_unparsable_rate_limit_file_gives_no_reading_and_no_error(store, gh, text):
    gh.add_run(100, **{LINUX_CHECK: {"rate_limit": text}})
    report = run_import(store, gh)
    assert report.runs_failed == 0 and report.written > 0 and readings(store) == []
    assert ended_of(store, LINUX_CHECK)["outcome"] == "passed"  # the run itself is judged as before


def test_the_reading_is_what_would_cross_asks_and_the_spend_follows_the_readings(store, gh):
    from orch_it.events import github_budget

    reset = BASE + timedelta(minutes=50)
    gh.add_run(100, **{LINUX_CHECK: {"rate_limit": rate_limit_text(400, reset)}})
    gh.add_run(101, created=120, **{LINUX_CHECK: {"rate_limit": rate_limit_text(150, reset)}})
    run_import(store, gh)
    assert len(readings(store)) == 2
    now = BASE + timedelta(minutes=20)
    assert github_budget.would_cross(REPO, 10, store=store, now=now, raw={}) is True  # 150 - 10 < 200 (20% of 1000)
    assert github_budget.actions_spend(REPO, store=store) == 850  # 1000 - 150 at the last reading (GitHub's used)
