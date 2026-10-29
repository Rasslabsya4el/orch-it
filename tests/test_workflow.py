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

from scan_repos import GITHUB_TOKEN, SOMEONE_HOME, Repo

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


def test_the_linux_job_lints_every_workflow_file_before_pytest_and_never_skips_it():
    steps = steps_of(job_lines(WORKFLOW.read_text("utf-8"))["linux"])
    lint = step_named(steps, "lint the workflows")
    assert re.search(r"^\s+run: actionlint$", "\n".join(lint), re.M)  # no file arguments: actionlint finds them all
    assert not any(line.strip().startswith("if:") for line in lint)
    assert steps.index(lint) < steps.index(step_named(steps, "pytest"))


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


# --- the job bounds (#411 unit 4): 15 minutes per change, the night's bound from measured full runs ---

PER_CHANGE_BOUND = 15
NIGHT_BOUND = {"linux": 27, "windows": 59}  # ceil(17.4 x 1.5), ceil(39.3 x 1.5)
MEASURED_MINUTES = {"linux": "17.4", "windows": "39.3"}  # the longest full run of each OS
MEASURED_RUNS = ("40870600022", "40870569933")  # the two dispatched runs the minutes come from
MEASURED_ON = "2026-10-29"


def timeout_of(text: str, job: str) -> tuple[str, list[str]]:
    """The value of a job's `timeout-minutes`, and the comment lines right above it."""

    raw = text.splitlines()
    start = raw.index(f"  {job}:")
    end = next((i for i in range(start + 1, len(raw)) if re.match(r"  \S", raw[i])), len(raw))
    at = next(i for i in range(start + 1, end) if raw[i].startswith("    timeout-minutes: "))
    comment = []
    i = at - 1
    while raw[i].strip().startswith("#"):
        comment.insert(0, raw[i].strip())
        i -= 1
    return raw[at].removeprefix("    timeout-minutes: "), comment


def test_each_job_keeps_the_per_change_bound_of_15_minutes_outside_the_night():
    text = WORKFLOW.read_text("utf-8")
    for job in ("linux", "windows"):
        value, _ = timeout_of(text, job)
        assert value.endswith(f"|| {PER_CHANGE_BOUND} }}}}"), (job, value)


def test_each_job_gives_the_measured_night_bound_only_on_the_nightly_condition():
    text = WORKFLOW.read_text("utf-8")
    night = re.fullmatch(r"\$\{\{ (.*) \}\}", workflow_env()["NIGHTLY"]).group(1)
    for job in ("linux", "windows"):
        value, _ = timeout_of(text, job)
        assert value == f"${{{{ ({night}) && {NIGHT_BOUND[job]} || {PER_CHANGE_BOUND} }}}}", (job, value)


def test_each_night_bound_carries_its_measurement_and_margin_beside_it():
    text = WORKFLOW.read_text("utf-8")
    for job in ("linux", "windows"):
        _, comment = timeout_of(text, job)
        comment_text = "\n".join(comment)
        assert "margin 1.5" in comment_text, job
        assert MEASURED_MINUTES[job] in comment_text, job
        assert f"gets {NIGHT_BOUND[job]} minutes" in comment_text, job
        assert all(run in comment_text for run in MEASURED_RUNS), job
        assert MEASURED_ON in comment_text, job


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
    linux = steps_of(jobs["linux"])
    step = step_named(linux, PRIVATE_DATA_STEP)
    text = "\n".join(step)
    assert "if: github.event_name == 'pull_request'" in text
    assert re.search(r"^\s+shell: bash$", text, re.M)
    assert step_env(step) == SCAN_ENV
    assert run_script(step).strip() == SCAN_COMMAND
    install = next(i for i, s in enumerate(linux) if any("pip install" in line for line in s))
    assert linux.index(step) > install
    assert PRIVATE_DATA_STEP not in "\n".join(jobs["windows"])


def test_the_closing_step_is_in_the_linux_job_only_for_pull_requests_between_the_scan_and_pytest():
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    linux = steps_of(jobs["linux"])
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
    assert closing not in "\n".join(jobs["windows"])


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
    in for a pull request of fake logins; `python` is the interpreter running the tests, on this
    checkout's source, and `gh` can reach nothing."""

    step = step_named(steps_of(job_lines(WORKFLOW.read_text("utf-8"))["linux"]), PRIVATE_DATA_STEP)
    filled = {"BASE": base, "HEAD": head, "AUTHOR": "fake-author", "OWNER": "fake-owner"}
    assert set(step_env(step)) == set(filled) | {"GH_TOKEN"}
    script = 'python() { "$PYTHON_FOR_TEST" "$@"; }\n' + run_script(step)
    quiet = {key: value for key, value in os.environ.items()
             if key not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")}
    env = quiet | filled | {"PYTHON_FOR_TEST": sys.executable, "PYTHONPATH": str(ROOT / "src"),
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


# a default host name, put together at run time so none stands in this committed file
DEFAULT_HOST_NAME = "WI" + "N-OHK3JQ5AM84"


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
