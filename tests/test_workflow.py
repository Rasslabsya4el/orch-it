"""The `tests` workflow as branch protection and the GitHub importer read it (#394 decision 8,
row 7): the required jobs keep their names `linux` and `windows`, each uploads its JUnit report,
and the `slow` and `live` marks run only in the nightly run."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def jobs(text: str) -> dict[str, str]:
    """Each top-level job's name and its block of lines."""

    found, name = {}, None
    inside = False
    for line in text.splitlines():
        if line.startswith("jobs:"):
            inside = True
            continue
        if inside and line and not line.startswith(" "):
            inside = False
        if not inside:
            continue
        head = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*", line)
        if head:
            name = head.group(1)
            found[name] = ""
        elif name:
            found[name] += line + "\n"
    return found


def test_the_required_jobs_keep_their_names():
    assert sorted(jobs(WORKFLOW.read_text("utf-8"))) == ["linux", "windows"]


def test_each_job_uploads_its_junit_report_even_when_red():
    for name, block in jobs(WORKFLOW.read_text("utf-8")).items():
        assert "--junitxml=junit.xml" in block, name
        assert "actions/upload-artifact@" in block, name
        assert "name: junit-${{ github.job }}" in block, name
        assert "if: always()" in block, name


def test_the_nightly_and_dispatch_runs_are_wired():
    text = WORKFLOW.read_text("utf-8")
    assert "schedule:" in text and "workflow_dispatch:" in text


def test_a_pull_request_run_leaves_the_slow_and_live_tests_out(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_marks.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.slow\ndef test_slow():\n    pass\n\n"
        "@pytest.mark.live\ndef test_live():\n    pass\n\n"
        "def test_plain():\n    pass\n", "utf-8")
    (tmp_path / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
    pr_marks = re.search(r'PR_MARKS: "([^"]+)"', WORKFLOW.read_text("utf-8")).group(1)

    def collected(*marks: str) -> int:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
             *marks, "suite"],
            capture_output=True, text=True, timeout=60, cwd=tmp_path,
            env=os.environ | {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
        assert done.returncode in (0, 5), done.stdout + done.stderr
        return sum(1 for line in done.stdout.splitlines() if "::" in line)

    assert collected("-m", pr_marks) == 1
    assert collected() == 3
