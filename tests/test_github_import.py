"""The GitHub importer (#394 decision 7; acceptance row 6): the runs of the `tests` workflow
become a `test_run` pair per required job, the queue wait, and one `test.result` per test case.
`gh` is a fake serving a recorded run set (JSON pages and artifact zips under tmp_path), so every
expected number below is read off the fixture's clock: a job is created at the run's base time,
starts 60 s later, its `pytest` step runs from +90 s to +330 s, and it completes at +360 s."""

import json
import math
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orch_it import settings
from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.testruns import github
from orch_it.testruns.github import GithubError, import_github

REPO = "Rasslabsya4el/orch-it"
BASE = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
GREEN = [("tests.test_a", "test_one", "passed"), ("tests.test_a", "test_two", "passed"),
         ("tests.test_b", "test_three", "skipped")]
RED = [("tests.test_a", "test_one", "passed"), ("tests.test_a", "test_two", "failed")]


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
        if path.endswith("/workflows/tests.yml/runs"):
            return self.pages("workflow_runs", self.runs)
        run_id = int(parts[5])
        if parts[-1] == "jobs":
            return self.pages("jobs", self.jobs[run_id])
        if parts[-1] == "artifacts":
            return self.pages("artifacts", self.artifacts.get(run_id, []))
        if parts[-1] == "timing":
            return json.dumps(self.timing.get(run_id, {"billable": {}}))
        raise AssertionError(args)

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

    def add_run(self, run_id, *, jobs=("linux", "windows"), created=0, status="completed",
                conclusion="success", event="pull_request", branch="feat/x", pr=7, attempt=1,
                billed=True, **per_job):
        """`per_job`: job name -> dict of `conclusion`, `cases` (the JUnit report; None: no
        artifact), `listed` (the artifact is listed), `expired`, `started` (False: never
        started), `attempt`, `artifact_name`, `billed_ms`."""

        base = BASE + timedelta(seconds=created)
        self.runs.append({
            "id": run_id, "status": status, "conclusion": conclusion, "event": event,
            "head_branch": branch, "head_sha": f"sha{run_id}", "run_attempt": attempt,
            "created_at": stamp(base, 0), "updated_at": stamp(base, 400),
            "pull_requests": [{"number": pr}] if pr else []})
        self.jobs[run_id], billable = [], []
        for index, name in enumerate(jobs):
            spec = {"conclusion": "success", "cases": GREEN, "expired": False, "started": True,
                    "attempt": attempt, "billed_ms": 300_000} | per_job.get(name, {})
            job_id = run_id * 10 + index + 1
            labels = ["ubuntu-latest"] if name == "linux" else ["windows-latest"]
            ran = spec["conclusion"] != "skipped"
            self.jobs[run_id].append({
                "id": job_id, "name": name, "status": "completed", "conclusion": spec["conclusion"],
                "run_attempt": spec["attempt"], "labels": labels,
                "created_at": stamp(base, 0), "started_at": stamp(base, 60) if spec["started"] else None,
                "completed_at": stamp(base, 360),
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

    os_name = {"linux": "linux", "windows": "windows"}[job_name]
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
    assert ended_of(store, "linux")["billed_minutes"] == 5
    assert ended_of(store, "windows")["billed_minutes"] == 2


def test_billed_minutes_are_0_when_the_repo_is_not_billed(store, gh):
    gh.add_run(100, billed=False)
    run_import(store, gh)
    assert ended_of(store, "linux")["billed_minutes"] == 0


def test_a_job_that_never_reached_pytest_has_no_run_minutes(store, gh):
    gh.add_run(100, linux={"conclusion": "failure", "cases": None})
    gh.jobs[100][0]["steps"] = gh.jobs[100][0]["steps"][:1]
    run_import(store, gh)
    ended = ended_of(store, "linux")
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
    assert ended_of(store, "linux")["collected"] == 3 and ended_of(store, "windows")["collected"] == 2


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
    ended = ended_of(store, "linux")
    assert (ended["outcome"], ended["reason"], ended["failed"]) == ("failed", "red", 1)
    assert ended_of(store, "windows")["outcome"] == "passed"


def test_a_failed_job_with_no_artifact_is_no_result_summary_missing(store, gh):
    gh.add_run(100, conclusion="failure", linux={"conclusion": "failure", "cases": None})
    report = run_import(store, gh)
    ended = ended_of(store, "linux")
    assert (ended["outcome"], ended["reason"], ended["summary_seen"]) == ("no_result", "summary_missing", False)
    assert report.reports_missing == 1


def test_a_failed_job_whose_report_is_all_green_is_no_result_summary_missing(store, gh):
    gh.add_run(100, conclusion="failure", linux={"conclusion": "failure", "cases": GREEN})
    run_import(store, gh)
    ended = ended_of(store, "linux")
    assert (ended["outcome"], ended["reason"]) == ("no_result", "summary_missing")


def test_a_successful_job_is_passed_whatever_its_report_says(store, gh):
    gh.add_run(100, linux={"cases": RED})
    run_import(store, gh)
    ended = ended_of(store, "linux")
    assert ended["outcome"] == "passed" and ended["failed"] == 1 and ended["collected"] == 2


def test_a_successful_job_with_no_artifact_stays_passed_with_no_rows(store, gh):
    gh.add_run(100, linux={"cases": None})
    report = run_import(store, gh)
    ended = ended_of(store, "linux")
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
    gh.add_run(101, created=600, branch="other")
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
    ended = ended_of(store, "linux")
    assert (ended["outcome"], ended["reason"]) == ("no_result", "cut")
    assert store.query("SELECT count(*) FROM events WHERE outcome = 'failed' AND kind = 'step.ended'"
                       ).rows == [(0,)]


@pytest.mark.parametrize("conclusion", ["neutral", "stale", "startup_failure", "action_required"])
def test_any_other_conclusion_is_no_result_summary_missing(store, gh, conclusion):
    gh.add_run(100, linux={"conclusion": conclusion, "cases": None})
    run_import(store, gh)
    ended = ended_of(store, "linux")
    assert (ended["outcome"], ended["reason"]) == ("no_result", "summary_missing")


def test_a_skipped_job_is_not_a_run_and_writes_nothing(store, gh):
    gh.add_run(100, linux={"conclusion": "skipped", "cases": None})
    run_import(store, gh)
    assert [started["run_os"] for started, _ in pairs(store).values()] == ["windows"]
    assert store.query("SELECT count(*) FROM events WHERE run LIKE 'github:100/1001/%'").rows == [(0,)]


# --- the runs ------------------------------------------------------------------------------------

def test_a_run_holding_only_linux_gets_a_job_missing_pair_for_windows(store, gh):
    gh.add_run(100, jobs=("linux",))
    run_import(store, gh)
    started, ended = pairs(store)["github:100/windows/1"]
    assert started["run_os"] == "windows" and started["place"] == "github"
    assert (ended["outcome"], ended["reason"], ended["collected"]) == ("no_result", "job_missing", 0)
    assert store.query("SELECT count(*) FROM events WHERE kind = 'test.result' AND"
                       " run = 'github:100/windows/1'").rows == [(0,)]


def test_a_missing_job_pair_runs_from_the_creation_to_the_last_update_of_the_run(store, gh):
    gh.add_run(100, jobs=("linux",))
    run_import(store, gh)
    rows = store.query("SELECT at FROM events WHERE step = 'test_run' AND run = 'github:100/windows/1'"
                       " ORDER BY at").rows
    assert rows == [("2026-10-26T10:00:00.000000Z",), ("2026-10-26T10:06:40.000000Z",)]


def test_a_job_that_ran_only_in_an_earlier_attempt_is_missing_from_the_latest(store, gh):
    gh.add_run(100, attempt=2, linux={"attempt": 1}, windows={"attempt": 2})
    run_import(store, gh)
    reasons = {run: ended["reason"] for run, (_, ended) in pairs(store).items()}
    assert reasons == {"github:100/1001/1": None, "github:100/1002/2": None,
                       "github:100/linux/2": "job_missing"}


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


def test_the_required_jobs_are_the_setting_github_required_jobs(store, gh):
    settings.save("github_required_jobs", ["linux"])
    gh.add_run(100)
    run_import(store, gh)
    assert [started["run_os"] for started, _ in pairs(store).values()] == ["linux"]


def test_a_required_job_that_is_no_list_of_names_is_a_bad_setting(store, gh):
    settings.save("github_required_jobs", "linux")
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
    assert ended_of(store, "linux")["collected"] == 3 and report.reports_missing == 0


def test_an_unreadable_report_is_no_result_summary_missing_for_a_failed_job(store, gh):
    gh.add_run(100, linux={"conclusion": "failure"})
    with zipfile.ZipFile(gh.zips / "100-junit-linux-1.zip", "w") as z:
        z.writestr("junit.xml", "<testsuites><testsuite><testcase")
    run_import(store, gh)
    assert ended_of(store, "linux")["reason"] == "summary_missing"


# --- again, and when gh fails --------------------------------------------------------------------

def test_a_second_import_writes_nothing_and_downloads_nothing(store, gh):
    gh.add_run(100)
    gh.add_run(101, created=3600, jobs=("linux",))
    first = run_import(store, gh)
    assert first.written > 0
    gh.calls.clear()
    second = run_import(store, gh)
    assert second.written == 0 and second.already_stored > 0
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


def test_gh_failing_on_a_run_is_a_coded_error_and_leaves_nothing_of_that_run(store, gh):
    gh.add_run(100, created=0)
    gh.add_run(101, created=3600)
    gh.fail_when = lambda args: args[0] == "run" and args[2] == "101"
    with pytest.raises(GithubError) as error:
        run_import(store, gh)
    assert error.value.code == "gh_failed"
    assert {run.split("/")[0] for run in pairs(store)} == {"github:100"}
    assert store.query("SELECT count(*) FROM events WHERE run LIKE 'github:101/%'").rows == [(0,)]


def test_the_import_after_a_failure_finishes_the_run_that_failed(store, gh):
    gh.add_run(100)
    gh.fail_when = lambda args: args[0] == "run"
    with pytest.raises(GithubError):
        run_import(store, gh)
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


def test_the_default_runner_turns_a_missing_gh_into_gh_failed(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(GithubError) as error:
        github.run_gh(["api", "x"])
    assert error.value.code == "gh_failed"


# --- the command ---------------------------------------------------------------------------------

def test_the_command_prints_the_counts_of_the_import(tmp_path, gh, monkeypatch, capsys):
    gh.add_run(100)
    monkeypatch.setattr(github, "run_gh", gh)
    assert main(["--store", str(tmp_path / "cli-store"), "events", "import-github"]) == 0
    printed = capsys.readouterr().out
    assert "runs_read=1" in printed and "jobs=2" in printed and "reports_missing=0" in printed
    assert "written=" in printed and "already_stored=0" in printed
    assert "repos/Rasslabsya4el/orch-it/" in " ".join(gh.calls[0])


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
    assert main(["--store", str(tmp_path / "s"), "events", "import-github"]) == 2
    assert capsys.readouterr().err.startswith("error: gh_failed")


# --- the real runs -------------------------------------------------------------------------------

@pytest.mark.live
def test_every_completed_tests_run_since_the_new_workflow_has_a_pair_per_required_job(tmp_path):
    """Runs the real `gh` against the repo (nightly only, where GH_TOKEN is set)."""

    if shutil.which("gh") is None:
        pytest.skip("no gh")
    auth = subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=60,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if auth.returncode != 0:
        pytest.skip("gh is not signed in")
    store = Store.open(tmp_path / "live-store")
    since = "2026-10-26T00:00:00Z"
    first = import_github(store, REPO, since=since)
    wanted = {name: name for name in ("linux", "windows")}
    runs = json.loads(github.run_gh(
        ["api", f"repos/{REPO}/actions/workflows/tests.yml/runs?per_page=100"]))["workflow_runs"]
    completed = [r for r in runs if r["status"] == "completed" and r["created_at"] >= since]
    assert first.runs_read >= len(completed)
    for run in completed:
        found = {started["run_os"] for run_id, (started, _) in pairs(store).items()
                 if run_id.startswith(f"github:{run['id']}/")}
        assert found >= set(wanted), run["id"]
    again = import_github(store, REPO, since=since)
    assert again.written == 0
