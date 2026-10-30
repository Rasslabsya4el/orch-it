"""The foreign run's script (#414, `.github/scripts/foreign_run.py`): the invented names of the copy and its origin, the
child's clean environment (no identity, no GitHub variables, a fresh home, no runner home on Windows), the bin rule on
POSIX, the suite's switch and selection, and the copy of the checkout with a local origin. The outside case of the
foreign run is shown here, not inferred: a planted test compares the checkout folder's name with the repo's name, fails
in the copy, passes where the folder carries the repo's name, and the private-data scan finds nothing in it.

Every sample name, path, account and token is built from parts at run time; the git identity is the scanner tests'
invented one (tests/scan_setup.py), so no value of the layer stands in this committed file.
"""

import importlib.util
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from orch_it.tickets.hook import repo_of
from scan_setup import INVENTED

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "foreign_run.py"
SPEC = importlib.util.spec_from_file_location("foreign_run", SCRIPT)
foreign_run = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = foreign_run  # the dataclass finds its module by name
SPEC.loader.exec_module(foreign_run)

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GIT_SECONDS = 60  # the bound of one git call in these tests
ON_WINDOWS = os.name == "nt"
# stand-ins for the real account, repo and checkout folder of the layer, built at run time
REAL_ACCOUNT = "real-owner-" + secrets.token_hex(4)
REAL_REPO = "real-repo-" + secrets.token_hex(4)
CHECKOUT_NAME = REAL_REPO
TOKEN = secrets.token_hex(8)
NAME = INVENTED[("git", "config", "user.name")]
EMAIL = INVENTED[("git", "config", "user.email")]
IDENTITY = {"GIT_AUTHOR_NAME": NAME, "GIT_AUTHOR_EMAIL": EMAIL, "GIT_COMMITTER_NAME": NAME, "GIT_COMMITTER_EMAIL": EMAIL}
IDENTITY_NAMES = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL")
# the planted test: it reads the repo's name from the origin and compares it with the name of the folder it runs in
PLANTED_NAME = "test_folder_name.py"
PLANTED = """\
import subprocess
from pathlib import Path, PurePath


def test_the_folder_is_named_as_the_repo():
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True).stdout
    repo = PurePath(url.strip()).name.removesuffix(".git")
    assert Path(__file__).absolute().parent.name == repo
"""


def draws(*values: str) -> Callable[[], str]:
    """A stand-in for the random draw: the values in order, one per call."""
    iterator = iter(values)
    return lambda: next(iterator)


def child_of(base: Path, path: str, os_name: str | None = None) -> dict[str, str]:
    """The child's environment as the script builds it, with the fresh home and the empty git configuration under `base`."""
    empty = base / "empty.gitconfig"
    if not empty.exists():
        empty.write_text("", "utf-8")
    return foreign_run.child_env(os.environ, home=base / "home", empty_config=empty, path=path,
                                 os_name=os_name or foreign_run.this_os())


def child_path(base: Path) -> str:
    """The child's PATH as the script makes it: the bin folder on POSIX (the same bin rule), the normal PATH on Windows."""
    if ON_WINDOWS:
        return os.environ["PATH"]
    bin_dir = base / "bin"
    foreign_run.make_bin(bin_dir, os.environ["PATH"])
    return str(bin_dir)


def write_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\n", "utf-8")
    path.chmod(0o755)


def planted_run(folder: Path, env: Mapping[str, str]) -> subprocess.CompletedProcess:
    """The planted test run the way the suite runs: the child's environment and the folder as cwd."""
    (folder / PLANTED_NAME).write_text(PLANTED, "utf-8")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", PLANTED_NAME],
                          cwd=folder, env=dict(env), capture_output=True, text=True, creationflags=NO_WINDOW)


def test_the_invented_names_differ_from_the_real_ones_and_from_each_other():
    for _ in range(50):  # the default draw is random: every draw keeps the rule
        names = foreign_run.invented_names(REAL_ACCOUNT, REAL_REPO, CHECKOUT_NAME)
        assert names.folder.casefold() not in {REAL_REPO.casefold(), CHECKOUT_NAME.casefold()}, names
        assert names.account.casefold() != REAL_ACCOUNT.casefold(), names
        assert names.repo.casefold() != REAL_REPO.casefold(), names


def test_a_draw_that_would_clash_with_the_real_names_is_drawn_again():
    # the first two draws would make the folder `tree-aa`, which is the real repo's name here: the next two stand
    names = foreign_run.invented_names(REAL_ACCOUNT, "tree-aa", CHECKOUT_NAME,
                                       draw=draws("aa", "bb", "cc", "dd", "ee", "ff"))
    assert names.folder == "tree-dd" and names.account == "acct-ee" and names.repo == "repo-ff"


def test_the_folder_name_differs_from_the_repo_name_the_copy_would_compare_it_with():
    names = foreign_run.invented_names(REAL_ACCOUNT, REAL_REPO, CHECKOUT_NAME)
    assert names.folder != names.repo
    assert names.folder != REAL_REPO and names.folder != CHECKOUT_NAME


def test_the_child_env_holds_no_identity_no_github_repository_and_a_fresh_home(tmp_path):
    home, empty = tmp_path / "home", tmp_path / "empty.gitconfig"
    source = {
        "PATH": "unused", "HOME": str(tmp_path / "real-home"), "TMPDIR": str(tmp_path),
        "GITHUB_REPOSITORY": f"{REAL_ACCOUNT}/{REAL_REPO}", "GITHUB_REPOSITORY_OWNER": REAL_ACCOUNT,
        "GITHUB_ACTOR": REAL_ACCOUNT, "CI": "true", "GH_TOKEN": TOKEN,
        "GIT_CONFIG_PARAMETERS": f"'user.name={NAME}'", "EMAIL": EMAIL, **IDENTITY,
    }
    env = foreign_run.child_env(source, home=home, empty_config=empty, path=str(tmp_path / "bin"),
                                os_name="posix")
    assert not [key for key in env if key.startswith("GITHUB_")], env
    assert not [key for key in IDENTITY_NAMES if key in env], env
    assert not {"CI", "GH_TOKEN", "GIT_CONFIG_PARAMETERS"} & set(env), env
    assert TOKEN not in env.values() and NAME not in env.values()
    assert env["HOME"] == str(home) and env["HOME"] != source["HOME"]
    assert env["GIT_CONFIG_GLOBAL"] == str(empty) and env["GIT_CONFIG_SYSTEM"] == str(empty)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["PATH"] == str(tmp_path / "bin")


def test_git_in_the_child_env_finds_no_identity(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git on this machine")
    env = child_of(tmp_path, path=os.environ["PATH"])
    for key in ("user.name", "user.email"):
        done = subprocess.run([git, "config", "--get", key], cwd=tmp_path, env=env, capture_output=True,
                              text=True, creationflags=NO_WINDOW)
        assert done.returncode == 1 and done.stdout == "", (key, done.stdout, done.stderr)


def test_the_windows_child_env_keeps_neither_the_runner_home_drive_nor_its_home_path(tmp_path):
    real_profile = tmp_path / "real-profile"
    source = {"PATH": "unused", "SYSTEMROOT": "unused", "USERPROFILE": str(real_profile),
              "HOMEDRIVE": "Z:", "HOMEPATH": str(real_profile)}
    env = foreign_run.child_env(source, home=tmp_path / "home", empty_config=tmp_path / "empty.gitconfig",
                                path="unused", os_name="nt")
    assert not {"HOMEDRIVE", "HOMEPATH"} & {key.upper() for key in env}, env
    assert env["USERPROFILE"] == str(tmp_path / "home")
    assert str(real_profile) not in env.values() and "Z:" not in env.values()


def test_the_windows_login_name_reaches_the_child_and_posix_gets_no_login_name_of_the_runner(tmp_path):
    # getpass has no fallback on Windows, so the private-data scan finds the machine's user value only in USERNAME
    # (without it the scan reports the kind `user` as not checked); on POSIX getpass falls back to the password
    # database, so USER and LOGNAME are not passed
    login = "login-" + secrets.token_hex(4)
    empty = tmp_path / "empty.gitconfig"
    win = foreign_run.child_env({"PATH": "unused", "Username": login}, home=tmp_path / "home", empty_config=empty,
                                path="unused", os_name="nt")
    assert win["Username"] == login
    posix = foreign_run.child_env({"PATH": "unused", "USER": login, "LOGNAME": login}, home=tmp_path / "home",
                                  empty_config=empty, path="unused", os_name="posix")
    assert not {"USER", "LOGNAME"} & set(posix), posix


@pytest.mark.parametrize("seconds, text", [(45, "45 seconds"), (120, "2 minutes"), (150, "2.5 minutes"),
                                           (60 * 60 * 2, "2 hours")])
def test_the_bound_is_written_for_a_human_not_as_a_count_of_seconds(seconds, text):
    assert foreign_run.bound_text(seconds) == text


@pytest.mark.parametrize("name, excluded", [
    ("python", True), ("python2", True), ("python3.12", True), ("python3-config", True),
    ("python3", False), ("pip", False), ("git", False),
])
def test_the_bin_rule_excludes_python_and_its_other_versions_but_not_python3(name, excluded):
    assert foreign_run.excluded_from_bin(name) is excluded


@pytest.mark.skipif(ON_WINDOWS, reason="the bin folder is POSIX: links to the executables of the normal PATH")
def test_on_linux_the_path_resolves_python3_and_git_and_not_python(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git on this machine")
    source = tmp_path / "normal-path"
    source.mkdir()
    for name in ("python", "python2", "python3.12", "python3", "unrelated-tool"):
        write_executable(source / name)
    os.symlink(git, source / "git")
    bin_dir = tmp_path / "bin"
    foreign_run.make_bin(bin_dir, str(source))
    env = child_of(tmp_path, path=str(bin_dir), os_name="posix")
    assert shutil.which("python3", path=env["PATH"]) is not None
    assert shutil.which("git", path=env["PATH"]) is not None
    assert shutil.which("unrelated-tool", path=env["PATH"]) is not None
    for name in ("python", "python2", "python3.12"):
        assert shutil.which(name, path=env["PATH"]) is None, name
    write_executable(source / "pip-script")  # a second call links what appeared since (a pip install's scripts)
    foreign_run.make_bin(bin_dir, str(source))
    assert shutil.which("pip-script", path=env["PATH"]) is not None


def test_the_suite_switch_reaches_the_child_only_when_the_job_sets_it(tmp_path):
    env = child_of(tmp_path, path="unused")
    assert "ORCH_IT_REQUIRE_SSHD" not in foreign_run.suite_switches(env, {"GH_TOKEN": TOKEN})
    assert foreign_run.suite_switches(env, {"ORCH_IT_REQUIRE_SSHD": "1"})["ORCH_IT_REQUIRE_SSHD"] == "1"
    assert "GH_TOKEN" not in foreign_run.suite_switches(env, {"GH_TOKEN": TOKEN})


def test_the_nightly_foreign_run_deselects_the_live_tests_and_a_change_run_keeps_its_marks():
    from test_workflow import workflow_env  # here, not at the top: test_workflow imports this file for `foreign_run`
    marks = workflow_env()["MARKS_LEFT_OUT_OF_CHANGE_RUNS"]  # the expression the workflow sets, not a copy of it
    assert foreign_run.selection(False, marks, os_name="posix") == marks
    assert foreign_run.selection(True, marks, os_name="posix") == foreign_run.NIGHTLY_SELECTION
    assert foreign_run.selection(True, marks, os_name="nt") == foreign_run.NIGHTLY_SELECTION
    assert "live" in foreign_run.NIGHTLY_SELECTION and "slow" not in foreign_run.NIGHTLY_SELECTION


def test_the_copy_is_a_tree_of_its_own_with_a_local_origin_and_no_credentials(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git on this machine")
    checkout = tmp_path / CHECKOUT_NAME
    checkout.mkdir()
    git_env = os.environ | IDENTITY
    subprocess.run([git, "init", "-q"], cwd=checkout, env=git_env, check=True,
                   creationflags=NO_WINDOW, timeout=GIT_SECONDS)
    (checkout / "notes.txt").write_text("one\n", "utf-8")
    subprocess.run([git, "add", "notes.txt"], cwd=checkout, env=git_env, check=True,
                   creationflags=NO_WINDOW, timeout=GIT_SECONDS)
    subprocess.run([git, "commit", "-q", "-m", "first"], cwd=checkout, env=git_env, check=True,
                   creationflags=NO_WINDOW, timeout=GIT_SECONDS)
    subprocess.run([git, "config", "--local", "http.https://github.com/.extraheader", "AUTHORIZATION: x"],
                   cwd=checkout, env=git_env, check=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS)
    head = subprocess.run([git, "rev-parse", "HEAD"], cwd=checkout, env=git_env, capture_output=True, text=True,
                          check=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS).stdout.strip()

    base = tmp_path / "base"
    base.mkdir()
    names = foreign_run.invented_names(REAL_ACCOUNT, REAL_REPO, CHECKOUT_NAME)
    env = child_of(base, path=os.environ["PATH"])
    copy = foreign_run.make_copy(checkout, base, names, env)

    assert copy.name == names.folder and copy != checkout
    assert (copy / "notes.txt").read_text("utf-8") == "one\n"
    copied_head = subprocess.run([git, "rev-parse", "HEAD"], cwd=copy, env=env, capture_output=True, text=True,
                                 check=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS).stdout.strip()
    assert copied_head == head
    url = subprocess.run([git, "remote", "get-url", "origin"], cwd=copy, env=env, capture_output=True, text=True,
                         check=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS).stdout.strip()
    bare = base / "remotes" / names.account / f"{names.repo}.git"
    assert bare.is_dir()
    # a stranger's checkout has a GitHub-shaped origin under its own account and repo, never the real ones
    assert url == f"https://github.com/{names.account}/{names.repo}.git"
    assert REAL_ACCOUNT not in url and REAL_REPO not in url
    # the product's own reading of the work tree's origin finds the invented owner/name
    assert repo_of(copy) == f"{names.account}/{names.repo}"
    # a push still lands in the local bare repo: nothing is sent to GitHub
    push_url = subprocess.run([git, "remote", "get-url", "--push", "origin"], cwd=copy, env=env, capture_output=True,
                              text=True, check=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS).stdout.strip()
    assert Path(push_url) == bare
    (copy / "second.txt").write_text("two\n", "utf-8")
    subprocess.run([git, "add", "second.txt"], cwd=copy, env=env, check=True,
                   creationflags=NO_WINDOW, timeout=GIT_SECONDS)
    subprocess.run([git, "commit", "-q", "-m", "second"], cwd=copy, env=env | IDENTITY, check=True,
                   creationflags=NO_WINDOW, timeout=GIT_SECONDS)
    pushed = subprocess.run([git, "push", "-q", "origin", "HEAD:refs/heads/probe"], cwd=copy, env=env,
                            capture_output=True, text=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS)
    assert pushed.returncode == 0, pushed.stderr
    landed = subprocess.run([git, "-C", str(bare), "rev-parse", "refs/heads/probe"], capture_output=True, text=True,
                            check=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS).stdout.strip()
    assert landed == subprocess.run([git, "rev-parse", "HEAD"], cwd=copy, env=env, capture_output=True, text=True,
                                    check=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS).stdout.strip()
    left = subprocess.run([git, "config", "--local", "--name-only", "--get-regexp", r"^http\."], cwd=copy, env=env,
                          capture_output=True, text=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS)
    assert left.stdout.split() == ["http.proxy"], left.stdout  # the token header is gone; a new http key fails this
    proxy = subprocess.run([git, "config", "--local", "http.proxy"], cwd=copy, env=env, capture_output=True,
                           text=True, check=True, creationflags=NO_WINDOW, timeout=GIT_SECONDS).stdout.strip()
    assert proxy == foreign_run.NO_NETWORK_PROXY
    # a fetch of the GitHub-shaped origin fails: the proxy refuses it at this machine, nothing is sent out
    fetched = subprocess.run([git, "fetch", "origin"], cwd=copy, env=env, capture_output=True, text=True,
                             creationflags=NO_WINDOW, timeout=GIT_SECONDS)
    assert fetched.returncode != 0


def test_the_planted_folder_check_fails_in_the_copy_and_passes_where_the_folder_has_the_repo_name(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git on this machine")
    # a plain checkout whose folder carries the repo's name and whose origin is the repo on GitHub
    checkout = tmp_path / "plain" / REAL_REPO
    checkout.mkdir(parents=True)
    git_env = os.environ | IDENTITY
    subprocess.run([git, "init", "-q"], cwd=checkout, env=git_env, check=True, creationflags=NO_WINDOW)
    subprocess.run([git, "remote", "add", "origin", f"https://github.com/{REAL_ACCOUNT}/{REAL_REPO}.git"],
                   cwd=checkout, env=git_env, check=True, creationflags=NO_WINDOW)
    (checkout / "notes.txt").write_text("one\n", "utf-8")
    subprocess.run([git, "add", "notes.txt"], cwd=checkout, env=git_env, check=True, creationflags=NO_WINDOW)
    subprocess.run([git, "commit", "-q", "-m", "first"], cwd=checkout, env=git_env, check=True,
                   creationflags=NO_WINDOW)

    base = tmp_path / "base"
    base.mkdir()
    env = child_of(base, path=child_path(base))
    names = foreign_run.invented_names(REAL_ACCOUNT, REAL_REPO, checkout.name)
    copy = foreign_run.make_copy(checkout, base, names, env)

    # the planted test in the copy, committed there the way the repo commits, then read by the private-data scan
    (copy / PLANTED_NAME).write_text(PLANTED, "utf-8")
    subprocess.run([git, "add", PLANTED_NAME], cwd=copy, env=env, check=True, creationflags=NO_WINDOW)
    subprocess.run([git, "commit", "-q", "-m", "plant"], cwd=copy, env=env | IDENTITY, check=True,
                   creationflags=NO_WINDOW)
    copy_run = planted_run(copy, env)
    assert copy_run.returncode != 0, copy_run.stdout + copy_run.stderr
    assert "1 failed" in copy_run.stdout, copy_run.stdout
    scan = subprocess.run([sys.executable, "-m", "orch_it", "guards", "scan", "--tree"], cwd=copy,
                          env=env | {"PYTHONPATH": str(SRC)}, capture_output=True, text=True,
                          creationflags=NO_WINDOW)
    assert scan.returncode == 0 and "hits=0" in scan.stdout, scan.stdout + scan.stderr

    plain_run = planted_run(checkout, env)
    assert plain_run.returncode == 0, plain_run.stdout + plain_run.stderr
    assert "1 passed" in plain_run.stdout, plain_run.stdout
