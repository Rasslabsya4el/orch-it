"""The `tests` workflow as branch protection and the GitHub importer read it (#394 decision 8,
row 7): the required jobs keep their names `linux` and `windows`, each uploads its JUnit report
even when red, and the `slow` and `live` marks run only in the nightly run.

The workflow is read with a small line reader (stdlib only, no YAML library): comments are
dropped, a job is a two-space-indented key under `jobs:`, a step a `- ` item of its job. The
pytest step's script is then run for real under bash with a stand-in `pytest`, so the test
checks the arguments the workflow passes, not the text it holds."""

import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

import pytest

from scan_repos import GITHUB_TOKEN, SOMEONE_HOME, Repo, default_host

from orch_it.testruns.config import DEFAULT_REQUIRED_CHECKS
from required_names import FOREIGN_LINUX, FOREIGN_MACOS, FOREIGN_WINDOWS, LINUX_CHECK, MACOS_OS, WINDOWS_CHECK
from test_foreign_run import foreign_run  # the script's module, loaded once by its own test file

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
JOB_HEAD = re.compile(r"  ['\"]?([A-Za-z0-9_-]+)['\"]?:")
# the jobs that run pytest themselves; the foreign jobs run `.github/scripts/foreign_run.py`
PYTEST_JOBS = (LINUX_CHECK, WINDOWS_CHECK)
FOREIGN_JOBS = (FOREIGN_LINUX, FOREIGN_WINDOWS, FOREIGN_MACOS)


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks


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


def pytest_job_lines() -> dict[str, list[str]]:
    return {name: lines for name, lines in job_lines(WORKFLOW.read_text("utf-8")).items() if name in PYTEST_JOBS}


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
    assert set(DEFAULT_REQUIRED_CHECKS) <= set(job_lines(WORKFLOW.read_text("utf-8")))


def test_the_linux_job_lints_every_workflow_file_before_pytest_and_never_skips_it():
    steps = steps_of(job_lines(WORKFLOW.read_text("utf-8"))[LINUX_CHECK])
    lint = step_named(steps, "lint the workflows")
    assert re.search(r"^\s+run: actionlint$", "\n".join(lint), re.M)  # no file arguments: actionlint finds them all
    assert not any(line.strip().startswith("if:") for line in lint)
    assert steps.index(lint) < steps.index(step_named(steps, "pytest"))


def test_each_job_uploads_its_junit_report_even_when_red():
    for name, lines in job_lines(WORKFLOW.read_text("utf-8")).items():
        steps = steps_of(lines)
        if name in PYTEST_JOBS:
            assert "--junitxml=junit.xml" in run_script(step_named(steps, "pytest")), name
        else:
            assert name in FOREIGN_JOBS, name
            assert "foreign_run.py" in "\n".join(step_named(steps, "the foreign run")), name
        upload = "\n".join(step_named(steps, "upload the JUnit report"))
        assert "uses: actions/upload-artifact@v4" in upload, name
        assert "if: always()" in upload, name
        if name in PYTEST_JOBS:  # the report and the rate-limit file beside it, one artifact
            assert re.search(r"^          path: \|\n            junit\.xml\n            actions-rate-limit\.json$", upload, re.M), name
            assert "if-no-files-found: ignore" in upload, name  # a missing file never fails the upload
        else:
            assert "path: junit.xml" in upload, name
        # a re-run of a job uploads under a new name, never onto the first attempt's
        assert "name: junit-${{ github.job }}-${{ github.run_attempt }}" in upload, name


PYTEST_STAND_IN = r"""
pytest() {
  { printf '%q ' "$@"; echo; } >> "$CALLS"
  local out="" phase=serial rc
  for a in "$@"; do
    case "$a" in --junitxml=*) out="${a#--junitxml=}";; -n) phase=parallel;; esac
  done
  if [ "$phase" = parallel ]; then rc="${RC_PARALLEL:-0}"; else rc="${RC_SERIAL:-5}"; fi
  if [ "$rc" = 5 ]; then
    printf '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="0"/></testsuites>' > "$out"
  else
    printf '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1"><testcase classname="c" name="%s"/></testsuite></testsuites>' "$phase" > "$out"
  fi
  return "$rc"
}
python() { "$PYTHON_FOR_TEST" "$@"; }
"""


def run_pytest_step(tmp_path, job, nightly, **extra):
    """The job's pytest step under bash with a stand-in `pytest` that records each call and writes a one-case
    report to the path of `--junitxml`; run in a copy of the repo's config. Returns the finished process and
    the recorded calls (one argument string per call)."""

    for leftover in ("calls.log", "junit.xml", "junit-serial.xml"):
        (tmp_path / leftover).unlink(missing_ok=True)
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(ROOT / ".github" / "scripts", tmp_path / ".github" / "scripts", dirs_exist_ok=True)
    script = PYTEST_STAND_IN + run_script(step_named(steps_of(job_lines(WORKFLOW.read_text("utf-8"))[job]), "pytest"))
    env = os.environ | workflow_env() | {"NIGHTLY": nightly, "CALLS": str(tmp_path / "calls.log"),
                                         "PYTHON_FOR_TEST": sys.executable, **extra}
    done = subprocess.run([a_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-c", script], capture_output=True,
                          text=True, timeout=60, env=env, cwd=tmp_path, creationflags=NO_WINDOW)
    calls = (tmp_path / "calls.log").read_text("utf-8").splitlines() if (tmp_path / "calls.log").exists() else []
    return done, calls


def call_args(call: str) -> list[str]:
    return shlex.split(call)


@pytest.mark.parametrize("nightly", ["true", "false"])
def test_only_the_nightly_run_takes_the_slow_and_live_tests(tmp_path, nightly):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_marks.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.slow\ndef test_slow():\n    pass\n\n"
        "@pytest.mark.live\ndef test_live():\n    pass\n\n"
        "@pytest.mark.serial\ndef test_serial():\n    pass\n\n"
        "@pytest.mark.slow\n"
        "@pytest.mark.serial\ndef test_slow_serial():\n    pass\n\n"
        "@pytest.mark.os_touching\ndef test_touching():\n    pass\n\n"
        "def test_plain():\n    pass\n", "utf-8")
    for job in PYTEST_JOBS:
        done, calls = run_pytest_step(tmp_path, job, nightly)
        assert done.returncode == 0, done.stderr
        ids = []
        for call in calls:
            args = call_args(call)
            marks = args[args.index("-m") + 1]
            collected = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-m", marks, "suite"],
                capture_output=True, text=True, timeout=60, cwd=tmp_path, creationflags=NO_WINDOW,
                env=os.environ | {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
            assert collected.returncode in (0, 5), collected.stdout + collected.stderr
            ids += [line.strip() for line in collected.stdout.splitlines() if "::" in line]
        expected = ["suite/test_marks.py::test_plain", "suite/test_marks.py::test_serial",
                    "suite/test_marks.py::test_touching"]
        if nightly == "true":
            expected += ["suite/test_marks.py::test_live", "suite/test_marks.py::test_slow",
                         "suite/test_marks.py::test_slow_serial"]
        if job == WINDOWS_CHECK and nightly == "false":  # the Windows change run takes only the marked tests
            expected = ["suite/test_marks.py::test_touching"]
        assert sorted(ids) == sorted(expected), job
        assert len(ids) == len(set(ids)), job  # no test runs in both phases


# --- the parallel run (#421 rules 1, 3, 5) ---------------------------------------------------

def test_every_job_runs_xdist_on_the_core_count_read_at_run_time_and_prints_it(tmp_path):
    for job, lines in pytest_job_lines().items():
        script = run_script(step_named(steps_of(lines), "pytest"))
        assert "os.cpu_count()" in script, job
        assert not re.search(r"(^|\s)-n[ =]?\d", script, re.M), job  # no core count written in the workflow
        done, calls = run_pytest_step(tmp_path, job, "false")
        assert done.returncode == 0, done.stderr
        cores = os.cpu_count()
        first = call_args(calls[0])
        assert first[first.index("-n") + 1] == str(cores), job
        assert first[first.index("--dist") + 1] == "worksteal", job  # a worker that runs dry takes a busy one's queued tests: the 60 s test cannot end the run late
        printed = done.stdout.splitlines()[0]  # before any test runs
        assert str(cores) in printed and "cores" in printed, done.stdout


def pyproject_bounds() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["tool"]["orch_it"]["tests"]


@pytest.mark.parametrize("nightly, key, seconds", [("false", "per_change_timeout_seconds", 120),
                                                   ("true", "nightly_timeout_seconds", 600)])
def test_every_test_is_bound_by_the_setting_of_its_run_kind_in_both_phases(tmp_path, nightly, key, seconds):
    assert pyproject_bounds()[key] == seconds
    for job, lines in pytest_job_lines().items():
        script = run_script(step_named(steps_of(lines), "pytest"))
        assert not re.search(r"\b(120|600)\b", script), job  # read from the config, not written here
        done, calls = run_pytest_step(tmp_path, job, nightly)
        assert done.returncode == 0, done.stderr
        assert len(calls) == 2, job
        for call in calls:
            args = call_args(call)
            assert args[args.index("--timeout") + 1] == str(seconds), (job, call)


def test_the_serial_phase_runs_after_the_parallel_one_and_the_job_has_one_report_with_both(tmp_path):
    for job in PYTEST_JOBS:
        done, calls = run_pytest_step(tmp_path, job, "true", RC_SERIAL="0")
        assert done.returncode == 0, done.stderr
        parallel, serial = (call_args(c) for c in calls)
        assert "-n" in parallel and "-n" not in serial, job
        assert parallel[parallel.index("-m") + 1].endswith("not serial"), job
        assert serial[serial.index("-m") + 1] == "serial", job
        report = ET.parse(tmp_path / "junit.xml").getroot()
        assert report.tag == "testsuites"
        assert sorted(case.get("name") for case in report.iter("testcase")) == ["parallel", "serial"], job


def test_no_serial_test_is_no_failure_but_a_red_test_in_either_phase_is(tmp_path):
    for job in PYTEST_JOBS:
        done, _ = run_pytest_step(tmp_path, job, "false", RC_SERIAL="5")
        assert done.returncode == 0, done.stderr  # pytest exit code 5, no tests collected
        assert [c.get("name") for c in ET.parse(tmp_path / "junit.xml").getroot().iter("testcase")] == ["parallel"]
        done, calls = run_pytest_step(tmp_path, job, "false", RC_PARALLEL="1", RC_SERIAL="0")
        assert done.returncode == 1, done.stderr
        assert len(calls) == 2, job  # the serial phase still runs when the parallel one is red
        done, _ = run_pytest_step(tmp_path, job, "false", RC_PARALLEL="0", RC_SERIAL="1")
        assert done.returncode == 1, done.stderr
        done, _ = run_pytest_step(tmp_path, job, "false", RC_PARALLEL="5", RC_SERIAL="0")
        assert done.returncode == 5, done.stderr  # nothing collected in the main phase is no pass


# --- a manual run takes the per-change kind unless asked (#421 rule 7) -----------------------

def test_a_manual_run_defaults_to_the_per_change_kind_and_the_schedule_stays_full():
    text = WORKFLOW.read_text("utf-8")
    block = re.search(r"^  workflow_dispatch:\n((?:    .*\n)+)", text, re.M).group(1)
    assert re.search(r"^        default: false$", block, re.M)
    assert "default: true" not in block
    header = "\n".join(text.splitlines()[:8])
    assert "slow_and_live=true" in header and "workflow_dispatch" in header
    night = re.fullmatch(r"\$\{\{ (.*) \}\}", workflow_env()["NIGHTLY"]).group(1)
    assert night == "github.event_name == 'schedule' || (github.event_name == 'workflow_dispatch' && inputs.slow_and_live)"


def test_the_linux_job_requires_the_real_sshd_test_to_run_and_the_windows_job_does_not():
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    linux = "\n".join(step_named(steps_of(jobs[LINUX_CHECK]), "pytest"))
    windows = "\n".join(step_named(steps_of(jobs[WINDOWS_CHECK]), "pytest"))
    assert re.search(r'^\s+ORCH_IT_REQUIRE_SSHD: "1"$', linux, re.M)
    assert "ORCH_IT_REQUIRE_SSHD" not in windows


def pytest_calls_of(script: str) -> list[str]:
    """The lines of a step's script that start a pytest call."""

    return [line.strip() for line in script.splitlines() if re.match(r"\s*pytest\s", line)]


def test_every_pytest_call_of_the_test_jobs_loads_the_ids_plugin_which_flags_placeholder_reads():
    for job, lines in pytest_job_lines().items():
        script = run_script(step_named(steps_of(lines), "pytest"))
        calls = pytest_calls_of(script)
        assert len(calls) == 2, job  # the parallel and the serial phase
        for call in calls:
            assert re.search(r"(^|\s)-p orch_it_ids(\s|$)", call), (job, call)


def test_the_pytest_step_of_each_test_job_sets_the_placeholder_list_from_a_repository_variable_and_the_plugin_path():
    for job, lines in pytest_job_lines().items():
        step = "\n".join(step_named(steps_of(lines), "pytest"))
        assert re.search(r"^\s+ORCH_IT_PLACEHOLDERS: \$\{\{ vars\.ORCH_IT_PLACEHOLDERS \}\}$", step, re.M), job
        assert re.search(r"^\s+PYTHONPATH: \$\{\{ github\.workspace \}\}/src/orch_it/testruns/pytest_plugin$", step, re.M), job


def test_the_plugin_path_the_workflow_sets_loads_the_plugin_in_a_real_pytest(tmp_path):
    step = "\n".join(step_named(steps_of(pytest_job_lines()[LINUX_CHECK]), "pytest"))
    value = re.search(r"^\s+PYTHONPATH: (.*)$", step, re.M).group(1).replace("${{ github.workspace }}", str(ROOT))
    (tmp_path / "test_x.py").write_text("def test_x():\n    pass\n", "utf-8")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "orch_it_ids", "-p", "no:cacheprovider"],
                          capture_output=True, text=True, timeout=60, cwd=tmp_path, creationflags=NO_WINDOW,
                          env={k: v for k, v in os.environ.items() if k != "ORCH_IT_PLACEHOLDERS"}
                          | {"PYTHONPATH": value})
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("job", PYTEST_JOBS)
def test_the_stand_in_pytest_is_called_with_the_ids_plugin_in_both_phases(tmp_path, job):
    done, calls = run_pytest_step(tmp_path, job, "false", ORCH_IT_PLACEHOLDERS="")
    assert done.returncode == 0, done.stderr
    assert len(calls) == 2
    for call in calls:
        args = call_args(call)
        assert args[args.index("-p") + 1] == "orch_it_ids", (job, call)


# --- the job bounds (#414 unit 18): each per-change bound is ceil(1.5 x the first measured job of its kind, in minutes) ---

# The first measured job of each kind (pull request 213, one run each): the job, its duration in seconds, the minutes its
# comment must name, and the per-change bound that the rule gives. The nightly bounds have no measurement yet: they keep
# today's values (NIGHTLY_BOUND) and say so in their comment (UNMEASURED).
MEASURED_PER_CHANGE = (
    (LINUX_CHECK, 156, "2.6 minutes", 4),  # 2:36
    (WINDOWS_CHECK, 164, "2.7 minutes", 5),  # 2:44
    (FOREIGN_LINUX, 357, "5.95 minutes", 9),  # 5:57
    (FOREIGN_WINDOWS, 456, "7.6 minutes", 12),  # 7:36
)
NIGHTLY_BOUND = {LINUX_CHECK: 16, WINDOWS_CHECK: 16, FOREIGN_LINUX: 11, FOREIGN_WINDOWS: 40}
MACOS_BOUND = 30
UNMEASURED = "unmeasured: set to 1.5 x the first nightly's job"


def timeout_of(text: str, job: str) -> tuple[str, list[str]]:
    """The value of a job's `timeout-minutes` (its inline comment cut off), and the comment lines: those right above
    the line and the inline comment itself."""

    raw = text.splitlines()
    start = raw.index(f"  {job}:")
    end = next((i for i in range(start + 1, len(raw)) if re.match(r"  \S", raw[i])), len(raw))
    at = next(i for i in range(start + 1, end) if raw[i].startswith("    timeout-minutes: "))
    comment = []
    i = at - 1
    while raw[i].strip().startswith("#"):
        comment.insert(0, raw[i].strip())
        i -= 1
    value, _, inline = raw[at].removeprefix("    timeout-minutes: ").partition("  #")
    if inline:
        comment.append("# " + inline.strip())
    return value.strip(), comment


def test_the_per_change_rule_is_the_ceiling_of_1_5_times_the_measured_minutes_in_the_table():
    for job, seconds, _, bound in MEASURED_PER_CHANGE:
        assert math.ceil(round(1.5 * seconds / 60, 6)) == bound, job


@pytest.mark.parametrize("job, seconds, minutes_text, bound", MEASURED_PER_CHANGE)
def test_each_job_gives_its_per_change_bound_outside_the_night_and_names_its_measured_minutes(
        job, seconds, minutes_text, bound):
    text = WORKFLOW.read_text("utf-8")
    night = re.fullmatch(r"\$\{\{ (.*) \}\}", workflow_env()["NIGHTLY"]).group(1)
    value, comment = timeout_of(text, job)
    assert value == f"${{{{ ({night}) && {NIGHTLY_BOUND[job]} || {bound} }}}}", (job, value)
    comment_text = " ".join(comment)
    assert minutes_text in comment_text, job
    assert "1.5" in comment_text, job
    assert not re.search(r"\d+ seconds?|\b\d+ s\b", comment_text), job  # durations are written in minutes


@pytest.mark.parametrize("job", [LINUX_CHECK, WINDOWS_CHECK, FOREIGN_LINUX, FOREIGN_WINDOWS])
def test_each_nightly_bound_is_unmeasured_and_says_so_beside_its_value(job):
    _, comment = timeout_of(WORKFLOW.read_text("utf-8"), job)
    assert UNMEASURED in " ".join(comment), job


def test_the_derived_wording_is_gone_from_the_workflow():
    text = WORKFLOW.read_text("utf-8")
    assert "derived" not in text
    assert "margin 1.5" not in text


# --- the scan of a pull request's commits (#395 row 7) ---------------------------------------

PRIVATE_DATA_STEP = "private data in the pull request's commits"
SCAN_COMMAND = ('python -m orch_it guards scan --range "$BASE..$HEAD" --machine-independent'
                ' --allow-email-of "$AUTHOR" --allow-email-of "$OWNER"')
SCAN_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "BASE": "${{ github.event.pull_request.base.sha }}",
    "HEAD": "${{ github.event.pull_request.head.sha }}",
    "AUTHOR": "${{ github.event.pull_request.user.login }}",
    "OWNER": "${{ github.repository_owner }}",
}
# what the runner gives the job's processes (GitHub Actions, Linux): invented names, built at run time
RUNNER_HOME = str(PurePosixPath("/home") / "runner-zz")
RUNNER_ENV = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "acme-x/widgets-y",
              "GITHUB_REPOSITORY_OWNER": "acme-x", "GITHUB_ACTOR": "acme-x",
              "HOME": RUNNER_HOME, "RUNNER_TEMP": str(PurePosixPath(RUNNER_HOME) / "work" / "_temp")}


def step_env(step: list[str]) -> dict[str, str]:
    """The `env:` mapping of a step: the `KEY: value` lines indented under its `env:` key."""

    head = next(i for i, line in enumerate(step) if line.strip() == "env:")
    indent = len(step[head]) - len(step[head].lstrip())
    env = {}
    for line in step[head + 1:]:
        if len(line) - len(line.lstrip()) <= indent:
            break
        key, _, value = line.strip().partition(": ")
        env[key] = value
    return env


def test_the_scan_step_is_in_the_linux_job_only_and_only_for_pull_requests():
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    linux = steps_of(jobs[LINUX_CHECK])
    step = step_named(linux, PRIVATE_DATA_STEP)
    text = "\n".join(step)
    assert "if: github.event_name == 'pull_request'" in text
    assert re.search(r"^\s+shell: bash$", text, re.M)
    assert step_env(step) == SCAN_ENV
    assert run_script(step).strip() == SCAN_COMMAND
    install = next(i for i, s in enumerate(linux) if any("pip install" in line for line in s))
    assert linux.index(step) > install
    assert PRIVATE_DATA_STEP not in "\n".join(jobs[WINDOWS_CHECK])


def test_the_closing_step_is_in_the_linux_job_only_for_pull_requests_between_the_scan_and_pytest():
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    linux = steps_of(jobs[LINUX_CHECK])
    closing = "closing words in the pull request's commits, title and body"
    step = step_named(linux, closing)
    text = "\n".join(step)
    assert "if: github.event_name == 'pull_request'" in text
    assert re.search(r"^\s+shell: bash$", text, re.M)
    assert step_env(step) == {
        "BASE_REF": "${{ github.event.pull_request.base.ref }}",
        "HEAD": "${{ github.event.pull_request.head.sha }}",
        "PR_TITLE": "${{ github.event.pull_request.title }}",
        "PR_BODY": "${{ github.event.pull_request.body }}",
    }
    assert run_script(step).strip() == 'python -m orch_it merge closing-check --base "$BASE_REF" --head "$HEAD"'
    assert linux.index(step_named(linux, PRIVATE_DATA_STEP)) < linux.index(step) < linux.index(step_named(linux, "pytest"))
    assert closing not in "\n".join(jobs[WINDOWS_CHECK])


def test_both_jobs_check_out_the_whole_history():
    for name, lines in job_lines(WORKFLOW.read_text("utf-8")).items():
        [checkout] = [s for s in steps_of(lines) if any("uses: actions/checkout@v4" in line for line in s)]
        assert re.search(r"^\s+fetch-depth: 0$", "\n".join(checkout), re.M), name


def pull_request_with(tmp_path, head_files):
    """A repository whose base commit holds a leak that was already there and whose head commit
    adds `head_files`: (the repository, the base sha, the head sha)."""

    repo = Repo(tmp_path / "repo")
    base = repo.commit({"old.txt": f"already on the base: {GITHUB_TOKEN}\n"})
    head = repo.commit(head_files, message="the pull request's work")
    return repo, base, head


def run_scan_step(tmp_path, repo, base, head):
    """The step's own script under bash, in the repository, with the step's environment filled
    in for a pull request of fake logins, and the runner's own environment (RUNNER_ENV) as GitHub
    gives it, so the values of the run are the runner's and none is this machine's; `python` is the
    interpreter running the tests, on this checkout's source, and `gh` can reach nothing."""

    step = step_named(steps_of(job_lines(WORKFLOW.read_text("utf-8"))[LINUX_CHECK]), PRIVATE_DATA_STEP)
    filled = {"BASE": base, "HEAD": head, "AUTHOR": "fake-author", "OWNER": "fake-owner"}
    assert set(step_env(step)) == set(filled) | {"GH_TOKEN"}
    script = 'python() { "$PYTHON_FOR_TEST" "$@"; }\n' + run_script(step)
    quiet = {key: value for key, value in os.environ.items()
             if key not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")}
    env = quiet | RUNNER_ENV | filled | {"PYTHON_FOR_TEST": sys.executable, "PYTHONPATH": str(ROOT / "src"),
                                         "GH_CONFIG_DIR": str(tmp_path / "gh"), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([a_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=repo.path,
                          env=env, creationflags=NO_WINDOW)


def test_the_scan_step_fails_on_a_pull_request_that_adds_private_data_without_printing_it(tmp_path):
    repo, base, head = pull_request_with(tmp_path, {
        "notes.txt": f"fine\n{GITHUB_TOKEN}\nsee {SOMEONE_HOME}/work\n"})
    done = run_scan_step(tmp_path, repo, base, head)
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stdout == (f"{head[:12]} content notes.txt:2 github_token\n"
                           f"{head[:12]} content notes.txt:3 home_path\nhits=2 commits=1\n")
    assert GITHUB_TOKEN not in done.stdout + done.stderr
    assert SOMEONE_HOME not in done.stdout + done.stderr
    assert "warning: email_of_unreadable: fake-author" in done.stderr


def test_the_scan_step_passes_a_pull_request_whose_own_commits_are_clean(tmp_path):
    repo, base, head = pull_request_with(tmp_path, {"notes.txt": "fine\nuses: actions/checkout@v4\n"})
    done = run_scan_step(tmp_path, repo, base, head)
    assert (done.returncode, done.stdout) == (0, "hits=0 commits=1\n"), done.stderr


# a default host name, made at run time (a WIN name of its shape) so none stands in this committed file
DEFAULT_HOST_NAME = default_host("WIN", 12)


def test_the_scan_step_fails_on_a_pull_request_that_adds_a_default_host_name_without_printing_it(tmp_path):
    repo, base, head = pull_request_with(tmp_path, {
        "run.xml": f'<testsuite name="x"\n  hostname="{DEFAULT_HOST_NAME}" tests="1"/>\n'})
    done = run_scan_step(tmp_path, repo, base, head)
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stdout == f"{head[:12]} content run.xml:2 host_name\nhits=1 commits=1\n"
    assert DEFAULT_HOST_NAME not in done.stdout + done.stderr


def test_the_scan_step_passes_names_that_only_look_like_a_default_host_name(tmp_path):
    lookalikes = f"WINDOWS and desktop and WIN-abc and MY-{DEFAULT_HOST_NAME} and Win-Server\n"
    repo, base, head = pull_request_with(tmp_path, {"notes.txt": lookalikes})
    done = run_scan_step(tmp_path, repo, base, head)
    assert (done.returncode, done.stdout) == (0, "hits=0 commits=1\n"), done.stderr


# --- the foreign run (#414) and the rate-limit file of the pytest jobs (#414) ------------------------------------------

def test_the_foreign_jobs_run_the_script_under_bash_and_upload_their_report():
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    for name in FOREIGN_JOBS:
        steps = steps_of(jobs[name])
        run = "\n".join(step_named(steps, "the foreign run"))
        assert re.search(r"^\s+shell: bash$", run, re.M), name
        assert re.search(r"^\s+run: python3? \.github/scripts/foreign_run\.py$", run, re.M), name
        upload = "\n".join(step_named(steps, "upload the JUnit report"))
        assert "if: always()" in upload and "path: junit.xml" in upload, name
        assert "name: junit-${{ github.job }}-${{ github.run_attempt }}" in upload, name


def test_the_foreign_linux_and_windows_jobs_run_on_every_change_as_the_suite_jobs_do():
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    for name in (FOREIGN_LINUX, FOREIGN_WINDOWS):
        assert not any(line.startswith("    if:") for line in jobs[name]), name  # no job-level condition


def test_the_foreign_macos_job_runs_on_the_nightly_schedule_only():
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    assert jobs[FOREIGN_MACOS].count("    if: github.event_name == 'schedule'") == 1


def test_the_pytest_jobs_save_the_rate_limit_after_pytest_and_never_fail_the_job():
    for job in PYTEST_JOBS:
        steps = steps_of(pytest_job_lines()[job])
        step = step_named(steps, "save the rate limit of the account")
        text = "\n".join(step)
        assert "continue-on-error: true" in text, job
        if job == LINUX_CHECK:
            assert re.search(r"^\s+run: gh api rate_limit > actions-rate-limit\.json \|\| rm -f actions-rate-limit\.json$",
                             text, re.M), job
        else:  # the Windows job's step is PowerShell: a failed call removes the file it made
            assert re.search(r"^\s+shell: pwsh$", text, re.M), job
            assert "gh api rate_limit > actions-rate-limit.json" in text, job
            assert "if ($LASTEXITCODE -ne 0) { Remove-Item" in text and "actions-rate-limit.json" in text, job
        assert steps.index(step) > steps.index(step_named(steps, "pytest")), job
        assert steps.index(step) < steps.index(step_named(steps, "upload the JUnit report")), job


@pytest.mark.parametrize("answer", ["ok", "fails"])
def test_a_failed_rate_limit_call_leaves_no_file_on_linux(tmp_path, answer):
    # the workflow's own command line, run under bash with a stand-in `gh` defined as a function (no PATH, no file)
    step = step_named(steps_of(pytest_job_lines()[LINUX_CHECK]), "save the rate limit of the account")
    command = re.search(r"^\s+run: (.*)$", "\n".join(step), re.M).group(1)
    stand_in = ("gh() { echo '{\"resources\":{}}'; }" if answer == "ok"
                else "gh() { echo 'partial'; return 1; }")
    done = subprocess.run([a_bash(), "--noprofile", "--norc", "-c", f"{stand_in}\n{command}"], cwd=tmp_path,
                          capture_output=True, text=True, creationflags=NO_WINDOW)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "actions-rate-limit.json").exists() is (answer == "ok")


def test_the_foreign_linux_job_passes_the_suite_switch_the_linux_pytest_step_sets_and_the_others_do_not():
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    linux = "\n".join(step_named(steps_of(jobs[LINUX_CHECK]), "pytest"))
    assert re.search(r'^\s+ORCH_IT_REQUIRE_SSHD: "1"$', linux, re.M)
    for name in (FOREIGN_LINUX, FOREIGN_WINDOWS, FOREIGN_MACOS):
        run = "\n".join(step_named(steps_of(jobs[name]), "the foreign run"))
        assert bool(re.search(r'^\s+ORCH_IT_REQUIRE_SSHD: "1"$', run, re.M)) is (name == FOREIGN_LINUX), name
        assert "GH_TOKEN" not in run, name  # the foreign setup holds no token


def test_the_foreign_macos_bound_is_unmeasured_and_keeps_its_value():
    value, comment = timeout_of(WORKFLOW.read_text("utf-8"), FOREIGN_MACOS)
    assert value == str(MACOS_BOUND), value
    assert UNMEASURED in " ".join(comment), value


@pytest.mark.parametrize("nightly, expected", [
    (True, "not live"),  # the nightly foreign run on Windows: the full suite, the live tests left out (foreign_run.py)
    (False, "os_touching and ({marks})"),  # a change run: the Windows-marked set among the marks left out of change runs
])
def test_the_foreign_windows_run_takes_the_full_suite_nightly_and_the_windows_marked_set_per_change(nightly, expected):
    marks = workflow_env()["MARKS_LEFT_OUT_OF_CHANGE_RUNS"]
    assert foreign_run.selection(nightly, marks, os_name="nt") == expected.format(marks=marks)


@pytest.mark.parametrize("job, os_name", [(LINUX_CHECK, "posix"), (WINDOWS_CHECK, "nt")])
def test_the_foreign_child_selects_per_change_as_the_ordinary_job_of_its_os_does(tmp_path, job, os_name):
    # the ordinary job's `-m` of its parallel phase, read from the step run for real; the foreign child runs one pytest
    # without the serial split, so the same set is `-m` without its trailing "and not serial"
    done, calls = run_pytest_step(tmp_path, job, "false")
    assert done.returncode == 0, done.stderr
    parallel = call_args(calls[0])
    ordinary = parallel[parallel.index("-m") + 1].removesuffix(" and not serial")
    if ordinary.startswith("(") and ordinary.endswith(")") and ordinary.count("(") == 1:
        ordinary = ordinary[1:-1]  # the workflow wraps the marks in parentheses before it adds a term
    marks = workflow_env()["MARKS_LEFT_OUT_OF_CHANGE_RUNS"]
    assert foreign_run.selection(False, marks, os_name=os_name) == ordinary, job
    assert ("os_touching" in ordinary) is (os_name == "nt"), job  # Windows takes the thin set only


