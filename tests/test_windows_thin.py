"""The thin Windows job (#414 unit 4, rows 5 and 8 of the ticket): a change run takes the Windows tests marked
`os_touching` only; the nightly run takes the whole Windows suite. A test that fails on the Windows nightly, passes
on Linux and carries no marker is named in a `needs-triage` ticket (`run_unmarked_check`); a marked test that fails
is red on the change run. The planted tests below run in pytest's own temporary folder, never in this repo."""

import tomllib
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
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
