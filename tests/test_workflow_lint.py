"""The workflow linter (#411): every file of `.github/workflows/` passes actionlint. The `linux` job of `tests`
runs it before pytest and the `test` extra installs it. The defect it guards: a job-level `env:` that reads the
`runner` context, which GitHub does not make available there, so every run of three workflows failed before a
job started. The linter runs as a subprocess; the probe files below are invented, never a real account or path."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

JOB_ENV_READS_RUNNER = """\
name: probe
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    env:
      PROBE_STORE: ${{ runner.temp }}/probe-events
    steps:
      - run: echo probe
"""

JOB_IF_READS_STEPS = """\
name: probe
on: push
jobs:
  build:
    if: ${{ steps.check.outputs.ready }}
    runs-on: ubuntu-latest
    steps:
      - run: echo probe
"""


def linter() -> str:
    """actionlint on the PATH, or the one pip put beside this interpreter."""

    found = shutil.which("actionlint")
    if found is not None:
        return found
    for folder in (Path(sys.executable).parent / "Scripts", Path(sys.executable).parent):
        for name in ("actionlint.exe", "actionlint"):
            if (folder / name).is_file():
                return str(folder / name)
    pytest.skip('actionlint is not installed: pip install -e ".[test]" installs it (actionlint-py)')


def lint(*files: Path) -> subprocess.CompletedProcess:
    return subprocess.run([linter(), *map(str, files)], capture_output=True, text=True, encoding="utf-8",
                          timeout=60, creationflags=NO_WINDOW)


def test_every_workflow_file_passes_the_linter():
    files = [*sorted(WORKFLOWS.glob("*.yml")), *sorted(WORKFLOWS.glob("*.yaml"))]
    assert files
    done = lint(*files)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_job_level_env_that_reads_the_runner_context_is_refused(tmp_path):
    probe = tmp_path / "job_env.yml"
    probe.write_text(JOB_ENV_READS_RUNNER, "utf-8")
    done = lint(probe)
    assert done.returncode == 1, done.stdout + done.stderr
    assert 'context "runner" is not allowed here' in done.stdout


def test_a_job_level_if_that_reads_the_steps_context_is_refused(tmp_path):
    probe = tmp_path / "job_if.yml"
    probe.write_text(JOB_IF_READS_STEPS, "utf-8")
    done = lint(probe)
    assert done.returncode == 1, done.stdout + done.stderr
    assert 'context "steps" is not allowed here' in done.stdout
