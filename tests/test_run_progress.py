"""Live progress of a test run (#406 decision 2, acceptance row 2): a running `test_run` writes `step.progressed`
{done, total, failed} at most every `test_progress_seconds`, `done` rising to the number of cases, and the last
point equals the end's counts. The fixture run is a stub own place that reports 200 results as they finish, on a
clock the test moves; the real plugin and a real pytest run are checked on their own.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

from orch_it.events.store import Store
from orch_it.settings import SettingsError
from orch_it.testruns import environment
from orch_it.testruns.config import TestSettings
from orch_it.testruns.places import PROGRESS_ENV, PlaceEnd, Request, read_progress
from orch_it.testruns.pytest_plugin import FOLDER as PLUGIN_FOLDER
from orch_it.testruns.pytest_plugin import orch_it_ids
from orch_it.testruns.runner import run_tests

REPO = "acme-x/widgets-y"
N_TESTS = 200
RED = (7, 88, 199)  # the 1-based cases that fail: three of them


class Clock:
    """A clock the test moves: the runner reads it, the stub advances it."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Streams:
    """An own place whose run reports each finished case to the runner (`request.progress`), 0.5 s of the fake clock
    per case, and writes a junit report of all of them at the end."""

    id = "own"
    os = environment.this_os()

    def __init__(self, clock, n=N_TESTS):
        self.clock, self.n = clock, n

    def run(self, request: Request, scratch):
        failed = 0
        for done in range(1, self.n + 1):
            self.clock.advance(0.5)
            failed += done in RED
            request.progress(self.n, done, failed)
        report = scratch / "junit.xml"
        cases = []
        for i in range(1, self.n + 1):
            body = '<failure message="boom"/>' if i in RED else ""
            cases.append(f'<testcase classname="test_streams" name="test_{i}">{body}</testcase>')
        report.write_text(
            f'<testsuites><testsuite name="streams" tests="{self.n}" failures="{len(RED)}" errors="0">'
            + "".join(cases) + "</testsuite></testsuites>", "utf-8")
        return PlaceEnd(report, False, False, 1, 0.0, 0.0)


def progressed(store):
    """The `step.progressed` points of the store, in the order they were written, as their detail dicts."""
    rows = store.query("SELECT detail FROM events WHERE kind = 'step.progressed' AND step = 'test_run'"
                       " ORDER BY rowid").rows
    return [json.loads(row[0]) for row in rows]


def test_a_200_case_run_writes_progress_at_most_every_interval_and_ends_on_its_counts(tmp_path):
    clock = Clock()
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=tmp_path, repo=REPO,
                     settings=TestSettings(test_progress_seconds=10.0),
                     places={"own": Streams(clock)}, clock=clock)
    assert (done.outcome, done.collected, done.failed) == ("failed", N_TESTS, len(RED))
    points = progressed(store)
    # a point each 10 s of the clock (0.5 s a case: every 20th case), counts from the stub's stream
    assert [p["done"] for p in points] == [1, 21, 41, 61, 81, 101, 121, 141, 161, 181, N_TESTS]
    assert all(p["total"] == N_TESTS for p in points)
    assert all(a["done"] < b["done"] for a, b in zip(points, points[1:]))
    assert points[-1] == {"done": N_TESTS, "total": N_TESTS, "failed": len(RED)}
    [(run, collected, failed)] = store.query(
        "SELECT run, collected, failed FROM events WHERE kind = 'step.ended' AND step = 'test_run'").rows
    assert (collected, failed) == (points[-1]["done"], points[-1]["failed"])
    assert {row[0] for row in store.query(
        "SELECT run FROM events WHERE kind = 'step.progressed' AND step = 'test_run'").rows} == {done.run}


def test_the_default_interval_is_10_seconds_and_a_setting_overrides_it():
    assert TestSettings().test_progress_seconds == 10.0
    assert TestSettings.from_settings({"test_progress_seconds": 2.5}, REPO).test_progress_seconds == 2.5
    with pytest.raises(SettingsError) as refused:
        TestSettings.from_settings({"test_progress_seconds": 0}, REPO)
    assert refused.value.code == "bad_setting"


def test_the_plugin_and_the_runner_agree_on_the_progress_file():
    assert orch_it_ids.ENV_PROGRESS == PROGRESS_ENV


def test_read_progress_takes_the_last_whole_line_and_none_when_there_is_none(tmp_path):
    path = tmp_path / "progress.txt"
    assert read_progress(path) is None
    path.write_text("200 0 0\n200 1 0\n200 2 1\n200 3", "utf-8")  # the last line is not whole yet
    assert read_progress(path) == (200, 2, 1)


@pytest.mark.parametrize("workers_args", [[], ["-n", "2"]], ids=["one-process", "xdist-workers"])
def test_the_plugin_writes_a_total_then_one_line_per_finished_test_on_the_controller_only(tmp_path, workers_args):
    root = tmp_path / "suite"
    root.mkdir()
    (root / "test_five.py").write_text(textwrap.dedent("""
        def test_a():
            assert True

        def test_b():
            assert True

        def test_c():
            assert False

        def test_d():
            assert True

        def test_e():
            assert True
        """), "utf-8")
    progress = tmp_path / "progress.txt"
    env = {**os.environ, "PYTHONPATH": str(PLUGIN_FOLDER), PROGRESS_ENV: str(progress)}
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "orch_it_ids", "-p", "no:cacheprovider",
                    *workers_args], cwd=root, env=env, capture_output=True, check=False)
    lines = progress.read_text("utf-8").splitlines()
    assert lines[0] == "5 0 0"
    assert len(lines) == 6  # the total, then one line per test: no worker writes its own
    assert lines[-1] == "5 5 1"
    assert read_progress(progress) == (5, 5, 1)


def test_a_real_own_run_ends_on_its_counts(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "test_real.py").write_text(textwrap.dedent("""
        def test_one():
            assert 1 + 1 == 2

        def test_two():
            assert False
        """), "utf-8")
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=root, repo=REPO, settings=TestSettings())
    assert (done.outcome, done.collected, done.failed) == ("failed", 2, 1)
    points = progressed(store)
    assert points and points[-1] == {"done": 2, "total": 2, "failed": 1}
