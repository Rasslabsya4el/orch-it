"""The `tests` workflow as branch protection and the GitHub importer read it (#394 decision 8,
row 7): the required jobs keep their names `linux` and `windows`, each uploads its JUnit report
even when red, and the `slow` and `live` marks run only in the nightly run.

The workflow is read with a small line reader (stdlib only, no YAML library): comments are
dropped, a job is a two-space-indented key under `jobs:`, a step a `- ` item of its job. The
pytest step's script is then run for real under bash with a stand-in `pytest`, so the test
checks the arguments the workflow passes, not the text it holds."""

import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
JOB_HEAD = re.compile(r"  ['\"]?([A-Za-z0-9_-]+)['\"]?:")


def without_comments(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(re.sub(r"\s+#.*$", "", line).rstrip())
    return lines


def job_lines(text: str) -> dict[str, list[str]]:
    """Job name -> the lines under its head, in the `jobs:` section."""

    by_job: dict[str, list[str]] = {}
    current = None
    in_jobs = False
    for line in without_comments(text):
        if not line:
            continue
        if not line.startswith(" "):
            in_jobs = line == "jobs:"
            current = None
            continue
        if not in_jobs:
            continue
        head = JOB_HEAD.fullmatch(line)
        if head:
            current = head.group(1)
            by_job[current] = []
        elif current:
            by_job[current].append(line)
    return by_job


def steps_of(lines: list[str]) -> list[list[str]]:
    """The job's steps, each its lines; a step starts at a `- ` item one level under `steps:`."""

    steps: list[list[str]] = []
    indent = None
    for line in lines:
        item = re.match(r"(\s*)- ", line)
        if item and (indent is None or len(item.group(1)) == indent):
            indent = len(item.group(1))
            steps.append([line])
        elif steps:
            steps[-1].append(line)
    return steps


def step_named(steps: list[list[str]], name: str) -> list[str]:
    [step] = [s for s in steps if any(re.search(rf"\bname: {re.escape(name)}$", line) for line in s)]
    return step


def run_script(step: list[str]) -> str:
    start = next(i for i, line in enumerate(step) if line.strip() == "run: |")
    return textwrap.dedent("\n".join(step[start + 1:])) + "\n"


def workflow_env() -> dict[str, str]:
    text = WORKFLOW.read_text("utf-8")
    block = re.search(r"^env:\n((?:  .*\n)+)", text, re.M).group(1)
    return {key: value.strip().strip('"') for key, value in re.findall(r"^  (\w+): (.*)$", block, re.M)}


def a_bash() -> str:
    found = shutil.which("bash")
    if found is None or "system32" in found.lower():  # WSL's bash is no stand-in for the runner's
        pytest.skip("no bash on this machine")
    return found


def test_the_required_jobs_keep_their_names():
    assert {"linux", "windows"} <= set(job_lines(WORKFLOW.read_text("utf-8")))


def test_each_job_uploads_its_junit_report_even_when_red():
    for name, lines in job_lines(WORKFLOW.read_text("utf-8")).items():
        steps = steps_of(lines)
        assert "--junitxml=junit.xml" in run_script(step_named(steps, "pytest")), name
        upload = "\n".join(step_named(steps, "upload the JUnit report"))
        assert "uses: actions/upload-artifact@v4" in upload, name
        assert "if: always()" in upload, name
        assert "path: junit.xml" in upload, name
        # a re-run of a job uploads under a new name, never onto the first attempt's
        assert "name: junit-${{ github.job }}-${{ github.run_attempt }}" in upload, name


@pytest.mark.parametrize("nightly", ["true", "false"])
def test_only_the_nightly_run_takes_the_slow_and_live_tests(tmp_path, nightly):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_marks.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.slow\ndef test_slow():\n    pass\n\n"
        "@pytest.mark.live\ndef test_live():\n    pass\n\n"
        "def test_plain():\n    pass\n", "utf-8")
    (tmp_path / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
    bash = a_bash()
    for name, lines in job_lines(WORKFLOW.read_text("utf-8")).items():
        script = 'pytest() { printf "%s\\n" "$@"; }\n' + run_script(step_named(steps_of(lines), "pytest"))
        env = os.environ | workflow_env() | {"NIGHTLY": nightly}
        passed = subprocess.run([bash, "-eo", "pipefail", "-c", script], capture_output=True,
                                text=True, timeout=60, env=env, creationflags=NO_WINDOW)
        assert passed.returncode == 0, passed.stderr
        args = [a for a in passed.stdout.splitlines() if not a.startswith("--junitxml")]
        collected = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider", *args, "suite"],
            capture_output=True, text=True, timeout=60, cwd=tmp_path, creationflags=NO_WINDOW,
            env=os.environ | {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
        assert collected.returncode == 0, collected.stdout + collected.stderr
        ids = sorted(line.strip() for line in collected.stdout.splitlines() if "::" in line)
        expected = ["suite/test_marks.py::test_plain"]
        if nightly == "true":
            expected = ["suite/test_marks.py::test_live", "suite/test_marks.py::test_plain",
                        "suite/test_marks.py::test_slow"]
        assert ids == expected, name


def test_the_linux_job_requires_the_real_sshd_test_to_run_and_the_windows_job_does_not():
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    linux = "\n".join(step_named(steps_of(jobs["linux"]), "pytest"))
    windows = "\n".join(step_named(steps_of(jobs["windows"]), "pytest"))
    assert re.search(r'^\s+ORCH_IT_REQUIRE_SSHD: "1"$', linux, re.M)
    assert "ORCH_IT_REQUIRE_SSHD" not in windows
