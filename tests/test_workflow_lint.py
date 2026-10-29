"""The workflow linter: every workflow file of the repo passes actionlint, with the same call the `linux` job of
`tests` makes (no file arguments, in the repo root), so the CI step and this test cover the same set. The defect
it guards: a job-level `env:` that reads the `runner` context, which GitHub does not make available there, so
every run of three workflows failed before a job started. Probes are invented, never a real account or path, and
each one sits in a temporary repo of its own under `.github/workflows/`."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
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

MISSING = 'actionlint is not installed: pip install -e ".[test]" installs it (actionlint-py)'


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def linter() -> str:
    """actionlint on the PATH, or the one pip put beside this interpreter. Missing: a failure when the CI
    variable is set (GitHub Actions sets it), a skip on a machine of one's own."""

    found = shutil.which("actionlint")
    if found is not None:
        return found
    for folder in (Path(sys.executable).parent / "Scripts", Path(sys.executable).parent):
        for name in ("actionlint.exe", "actionlint"):
            if (folder / name).is_file():
                return str(folder / name)
    if os.environ.get("CI"):
        pytest.fail(MISSING + " - in CI a missing linter fails the run", pytrace=False)
    pytest.skip(MISSING)


def lint(root: Path) -> subprocess.CompletedProcess:
    """actionlint with no file arguments in `root`: it lints every workflow file of `root/.github/workflows`,
    the same call as the CI step `lint the workflows`."""

    return subprocess.run([linter()], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          timeout=60, creationflags=NO_WINDOW)


def probe_repo(tmp_path: Path, name: str, text: str) -> Path:
    """A repository of its own: actionlint with no arguments looks for the project root by its `.git`."""

    (tmp_path / ".git").mkdir()
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / name).write_text(text, "utf-8")
    return tmp_path


def test_every_workflow_file_passes_the_linter():
    assert list((ROOT / ".github" / "workflows").glob("*.yml"))
    done = lint(ROOT)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_yaml_workflow_is_linted_too(tmp_path):
    done = lint(probe_repo(tmp_path, "job_env.yaml", JOB_ENV_READS_RUNNER))
    assert done.returncode == 1, done.stdout + done.stderr
    assert 'context "runner" is not allowed here' in done.stdout


def test_a_job_level_env_that_reads_the_runner_context_is_refused(tmp_path):
    done = lint(probe_repo(tmp_path, "job_env.yml", JOB_ENV_READS_RUNNER))
    assert done.returncode == 1, done.stdout + done.stderr
    assert 'context "runner" is not allowed here' in done.stdout


def test_a_job_level_if_that_reads_the_steps_context_is_refused(tmp_path):
    done = lint(probe_repo(tmp_path, "job_if.yml", JOB_IF_READS_STEPS))
    assert done.returncode == 1, done.stdout + done.stderr
    assert 'context "steps" is not allowed here' in done.stdout


def without_actionlint(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))  # no Scripts folder beside it


def test_a_missing_actionlint_fails_in_ci(monkeypatch, tmp_path):
    without_actionlint(monkeypatch, tmp_path)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="actionlint is not installed"):
        linter()


def test_a_missing_actionlint_skips_on_a_machine_of_one_s_own(monkeypatch, tmp_path):
    without_actionlint(monkeypatch, tmp_path)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception, match="actionlint is not installed"):
        linter()
