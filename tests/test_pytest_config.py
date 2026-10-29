"""The repo's pytest settings the `tests` workflow reads (#421): the per-test time bound of each run kind, the
`serial` mark for a test that cannot share a machine, and the plugins the parallel, bounded run needs."""

import pytest
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def test_the_test_extra_holds_the_parallel_and_the_per_test_bound_plugins():
    extra = " ".join(PYPROJECT["project"]["optional-dependencies"]["test"])
    assert "pytest-xdist" in extra and "pytest-timeout" in extra


def test_a_per_test_bound_is_set_for_both_run_kinds():
    bounds = PYPROJECT["tool"]["orch_it"]["tests"]
    assert (bounds["per_change_timeout_seconds"], bounds["nightly_timeout_seconds"]) == (120, 600)


def run_collect(tmp_path, marker: str) -> subprocess.CompletedProcess:
    (tmp_path / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
    (tmp_path / "test_one.py").write_text("import pytest\n\n"
                                                f"@pytest.mark.{marker}\ndef test_one():\n    pass\n", "utf-8")
    return subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "--strict-markers",
                           "-p", "no:cacheprovider", "test_one.py"], capture_output=True, text=True, timeout=60, cwd=tmp_path,
                          creationflags=NO_WINDOW, env=os.environ | {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})


def test_a_serial_marked_test_is_registered_under_strict_markers(tmp_path):
    done = run_collect(tmp_path, "serial")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "test_one.py::test_one" in done.stdout


def test_a_mark_nobody_registered_is_still_refused(tmp_path):
    done = run_collect(tmp_path, "not_a_registered_mark")
    assert done.returncode != 0
    assert "not_a_registered_mark" in done.stdout + done.stderr
