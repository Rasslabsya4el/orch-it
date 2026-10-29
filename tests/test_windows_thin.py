"""The thin Windows job (#414 unit 4, rows 5 and 8 of the ticket): a change run takes the Windows tests marked
`os_touching` only; the nightly run takes the whole Windows suite. A test that fails on the Windows nightly, passes
on Linux and carries no marker is named in a `needs-triage` ticket (`run_unmarked_check`); a marked test that fails
is red on the change run. The planted tests below run in pytest's own temporary folder, never in this repo."""

import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fake_github import FAILED, PASSED, REPO, FakeGitHub
from required_names import LINUX_CHECK, WINDOWS_CHECK

from orch_it.events.store import Store
from orch_it.merge.hub import Result
from orch_it.testruns import junit
from orch_it.testruns.config import windows_thin
from orch_it.testruns.windows_unmarked import post_windows_time, run_unmarked_check

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
CONFTEST = ROOT / "tests" / "conftest.py"
CHANGE_SELECTION = "os_touching and (not slow and not live)"  # the Windows change run's `-m` expression (the workflow)


def planted(pytester, body: str) -> None:
    """A throwaway suite with the three marks the repo registers, in the test's own folder."""

    pytester.makeini("[pytest]\nmarkers =\n    os_touching: x\n    slow: x\n    live: x\n    serial: x\n")
    pytester.makepyfile(test_planted=body)


def test_the_marker_is_registered_in_pyproject():
    markers = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["pytest"]["ini_options"]["markers"]
    assert any(line.startswith("os_touching:") for line in markers)


def test_a_failing_marked_test_is_red_on_the_change_run_selection(pytester):
    planted(pytester, "import pytest\n\n"
                      "@pytest.mark.os_touching\ndef test_marked():\n    assert False\n\n"
                      "def test_plain():\n    pass\n")
    result = pytester.runpytest("-m", CHANGE_SELECTION)
    result.assert_outcomes(failed=1, deselected=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_an_unmarked_windows_only_failure_is_green_on_the_change_run_and_red_on_the_full_set(pytester):
    planted(pytester, "def test_windows_only():\n    assert False\n")
    change = pytester.runpytest("-m", CHANGE_SELECTION)
    assert change.ret == pytest.ExitCode.NO_TESTS_COLLECTED  # nothing selected: the change run is green
    full = pytester.runpytest()
    full.assert_outcomes(failed=1)
    assert full.ret == pytest.ExitCode.TESTS_FAILED


def test_the_windows_job_keeps_its_name_and_its_change_run_takes_only_the_marked_tests():
    text = WORKFLOW.read_text("utf-8")
    assert "\n  linux:\n" in text and "\n  windows:\n" in text  # branch protection names these jobs
    assert 'select="os_touching and ($MARKS_LEFT_OUT_OF_CHANGE_RUNS) and "' in text  # the Windows change run
    assert text.count('select="($MARKS_LEFT_OUT_OF_CHANGE_RUNS) and "') == 1  # linux stays the whole change suite
    assert text.count('select=""') == 2  # the nightly run takes everything, one line per job


# --- the nightly's ticket for an unmarked Windows-only failure (row 5) ---

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
SETTINGS = {"repo": REPO}
TEST = "tests/test_paths.py::test_a_windows_path"
NIGHTLY_JOBS_RED = {LINUX_CHECK: PASSED, WINDOWS_CHECK: FAILED}


@pytest.fixture
def world(tmp_path):
    return FakeGitHub(NOW), Store.open(tmp_path / "events")


def night(hub, results, *, jobs=None, run_id=None):
    """A nightly run of the `tests` workflow on main whose reports hold `results`."""

    hub.later(days=1)
    return hub.main_run(hub.land(), jobs or NIGHTLY_JOBS_RED, event="schedule", run_id=run_id, results=results)


def windows_red(test_id=TEST, *, marked=False, also_linux=False):
    results = [Result(WINDOWS_CHECK, test_id, "failed", marked), Result(LINUX_CHECK, test_id, "passed", False)]
    if also_linux:
        results[1] = Result(LINUX_CHECK, test_id, "failed", False)
    return results


def triage(hub):
    return hub.issues_with_label(REPO, "needs-triage")


def test_a_windows_nightly_failure_that_passes_on_linux_with_no_marker_opens_one_ticket_naming_the_test(world):
    hub, store = world
    night(hub, windows_red())
    report = run_unmarked_check(store, hub, SETTINGS)
    [number] = triage(hub)
    assert report.opened == [number] and report.commented == []
    issue = hub.issues[(REPO, number)]
    assert TEST in issue.body and TEST in issue.title and "os_touching" in issue.body
    assert set(issue.labels) == {"needs-triage"}


def test_a_second_night_comments_on_the_open_ticket_and_opens_none(world):
    hub, store = world
    night(hub, windows_red())
    [number] = run_unmarked_check(store, hub, SETTINGS).opened
    night(hub, windows_red())
    report = run_unmarked_check(store, hub, SETTINGS)
    assert report.opened == [] and report.commented == [number] and triage(hub) == [number]
    assert TEST in hub.comments(REPO, number)[0].body
    again = run_unmarked_check(store, hub, SETTINGS)  # the same night looked at twice says nothing twice
    assert again.opened == [] and again.commented == [] and len(hub.comments(REPO, number)) == 1


def test_a_marked_test_opens_no_ticket(world):
    hub, store = world
    night(hub, windows_red(marked=True))
    report = run_unmarked_check(store, hub, SETTINGS)
    assert report.opened == [] and report.commented == [] and triage(hub) == []


def test_a_test_that_also_fails_on_linux_opens_no_ticket(world):
    hub, store = world
    night(hub, windows_red(also_linux=True), jobs={LINUX_CHECK: FAILED, WINDOWS_CHECK: FAILED})
    assert run_unmarked_check(store, hub, SETTINGS).opened == [] and triage(hub) == []


def test_two_unmarked_failures_get_a_ticket_each_and_a_per_change_run_is_not_a_night(world):
    hub, store = world
    hub.main_run(hub.land(), NIGHTLY_JOBS_RED, event="push", results=windows_red("t::on_a_push"))
    assert run_unmarked_check(store, hub, SETTINGS).opened == []
    night(hub, windows_red("t::one") + windows_red("t::two"))
    assert len(run_unmarked_check(store, hub, SETTINGS).opened) == 2


def test_a_marked_test_carries_the_marker_in_its_report_and_the_reader_sees_it(pytester):
    """The repo's conftest writes `os_touching` into a marked test's report; the JUnit reader reads it as `marked`."""

    planted(pytester, "import pytest\n\n"
                      "@pytest.mark.os_touching\ndef test_marked():\n    pass\n\n"
                      "def test_plain():\n    pass\n")
    pytester.makeconftest(CONFTEST.read_text("utf-8"))
    report = pytester.path / "report.xml"
    pytester.runpytest("--junitxml", str(report)).assert_outcomes(passed=2)
    assert {case.name: case.marked for case in junit.read(report)} == {"test_marked": True, "test_plain": False}


# --- the per-change Windows time against its target (row 5) ---

THIN = windows_thin({}, REPO)


def envelope(**over):
    base = {"ticket": None, "role": "github", "session": None, "parent_session": None,
            "turn": None, "note": None, "repo": REPO}
    return base | over


def at_minute(minute: float) -> str:
    moment = datetime(2026, 10, 26, 10, 0, tzinfo=UTC) + timedelta(minutes=minute)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def windows_run(store, n, minutes, *, selection="full", tests=(), os_name=THIN.os):
    """An ended GitHub test run on `os_name` whose pytest took `minutes`, with a `test.result` per `tests` (id, ms)."""

    run = f"gh-{os_name}-{selection}-{n}"
    events = [envelope(kind="step.started", step="test_run", run=run, at=at_minute(10 * n),
                       detail={"place": "github", "run_os": os_name, "selection": selection})]
    events += [envelope(kind="test.result", step="test_run", run=run, at=at_minute(10 * n + 1),
                        detail={"test_id": test_id, "outcome": "passed", "skip_reason": None, "ms": ms, "attempt": 1})
               for test_id, ms in tests]
    events.append(envelope(kind="step.ended", step="test_run", run=run, at=at_minute(10 * n + 2),
                           detail={"outcome": "passed", "run_minutes": minutes, "reason": None}))
    with store.writer() as writer:
        for event in events:
            writer.write(event)
    return run


def test_three_per_change_runs_under_the_target_post_nothing(world):
    hub, store = world
    for n in range(3):
        windows_run(store, n, THIN.change_minutes - 0.5)
    done = post_windows_time(store, hub, SETTINGS)
    assert done.over is False and done.median == THIN.change_minutes - 0.5 and len(done.runs) == THIN.runs
    assert triage(hub) == []


def test_fewer_than_three_runs_are_no_verdict_and_a_nightly_run_is_not_a_per_change_run(world):
    hub, store = world
    windows_run(store, 0, 9.0)
    windows_run(store, 1, 9.0)
    windows_run(store, 2, 40.0, selection="nightly")
    windows_run(store, 3, 9.0, os_name="macos")
    done = post_windows_time(store, hub, SETTINGS)
    assert done.median is None and done.over is False and triage(hub) == []


def test_a_median_over_the_target_names_the_slowest_tests_in_one_ticket_and_later_comments(world):
    hub, store = world
    slow = [(f"tests/test_p.py::test_{n}", 1000 * n) for n in range(1, 9)]  # 1 s ... 8 s
    for n in range(3):
        windows_run(store, n, THIN.change_minutes + 1, tests=slow)
    done = post_windows_time(store, hub, SETTINGS)
    assert done.over and done.median == THIN.change_minutes + 1 and done.opened
    [number] = triage(hub)
    assert done.ticket == number
    body = hub.issues[(REPO, number)].body
    assert [test_id for test_id, _ in done.slowest] == [f"tests/test_p.py::test_{n}" for n in range(8, 8 - THIN.slowest, -1)]
    assert "tests/test_p.py::test_8" in body and "tests/test_p.py::test_1`" not in body
    windows_run(store, 3, THIN.change_minutes + 1, tests=slow)  # a newer run: the next check comments, opens none
    again = post_windows_time(store, hub, SETTINGS)
    assert again.opened is False and again.ticket == number and triage(hub) == [number]
    assert "tests/test_p.py::test_8" in hub.comments(REPO, number)[0].body
    post_windows_time(store, hub, SETTINGS)  # the same newest run says nothing twice
    assert len(hub.comments(REPO, number)) == 1
