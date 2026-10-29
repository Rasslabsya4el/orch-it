"""The foreign run's script (#414 unit 10, `.github/scripts/foreign_run.py`): the invented names of the copy and its
origin, the child's clean environment (no identity, no GitHub variables, a fresh home), the bin folder that holds
python3 and not `python` on POSIX, and the copy of the checkout with a local origin. Every sample name, path and
account is built from parts at run time, so none stands in this committed file."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "foreign_run.py"
SPEC = importlib.util.spec_from_file_location("foreign_run", SCRIPT)
foreign_run = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = foreign_run  # the dataclass finds its module by name
SPEC.loader.exec_module(foreign_run)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# stand-ins for the real account and repo of the layer, and for a checkout folder named as the repo is on GitHub
REAL_ACCOUNT = "-".join(("owner", "x"))
REAL_REPO = "-".join(("orch", "it"))
CHECKOUT_NAME = REAL_REPO
IDENTITY_NAMES = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL")
ON_WINDOWS = os.name == "nt"


def draws(*values: str):
    """A stand-in for the random draw: the values in order, one per call."""
    iterator = iter(values)
    return lambda: next(iterator)


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
    # row 12's case: a comparison of the checkout folder's name with the repo's name must fail inside the copy
    names = foreign_run.invented_names(REAL_ACCOUNT, REAL_REPO, CHECKOUT_NAME)
    assert names.folder != names.repo
    assert names.folder != REAL_REPO and names.folder != CHECKOUT_NAME


def test_the_child_env_holds_no_identity_no_github_repository_and_a_fresh_home(tmp_path):
    home, empty = tmp_path / "home", tmp_path / "empty.gitconfig"
    source = {
        "PATH": "unused", "HOME": str(tmp_path / "real-home"), "TMPDIR": str(tmp_path),
        "GITHUB_REPOSITORY": f"{REAL_ACCOUNT}/{REAL_REPO}", "GITHUB_REPOSITORY_OWNER": REAL_ACCOUNT,
        "GITHUB_ACTOR": REAL_ACCOUNT, "CI": "true", "GH_TOKEN": "not-a-token",
        "GIT_CONFIG_PARAMETERS": "'user.name=x'", "EMAIL": "x@x.invalid",
        **{name: "given" for name in IDENTITY_NAMES},
    }
    env = foreign_run.child_env(source, home=home, empty_config=empty, path=str(tmp_path / "bin"),
                                os_name="posix")
    assert not [key for key in env if key.startswith("GITHUB_")], env
    assert not [key for key in IDENTITY_NAMES if key in env], env
    assert not {"CI", "GH_TOKEN", "GIT_CONFIG_PARAMETERS"} & set(env), env
    assert env["HOME"] == str(home) and env["HOME"] != source["HOME"]
    assert env["GIT_CONFIG_GLOBAL"] == str(empty) and env["GIT_CONFIG_SYSTEM"] == str(empty)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["PATH"] == str(tmp_path / "bin")


def test_git_in_the_child_env_finds_no_identity(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git on this machine")
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", "utf-8")
    env = foreign_run.child_env(os.environ, home=tmp_path / "home", empty_config=empty,
                                path=os.environ["PATH"], os_name=foreign_run.this_os())
    for key in ("user.name", "user.email"):
        done = subprocess.run([git, "config", "--get", key], cwd=tmp_path, env=env, capture_output=True,
                              text=True, creationflags=NO_WINDOW)
        assert done.returncode == 1 and done.stdout == "", (key, done.stdout, done.stderr)


@pytest.mark.skipif(ON_WINDOWS, reason="the bin folder is POSIX: links to python3's folder and the tools")
def test_on_linux_the_path_resolves_python3_and_not_python(tmp_path):
    python_dir = tmp_path / "python-folder"
    python_dir.mkdir()
    for name in ("python", "python3"):
        (python_dir / name).write_text("#!/bin/sh\n", "utf-8")
        (python_dir / name).chmod(0o755)
    bin_dir = tmp_path / "bin"
    foreign_run.make_bin(bin_dir, python_dir, tools={})
    env = foreign_run.child_env(os.environ, home=tmp_path / "home", empty_config=tmp_path / "e",
                                path=str(bin_dir), os_name="posix")
    assert shutil.which("python3", path=env["PATH"]) is not None
    assert shutil.which("python", path=env["PATH"]) is None


def test_the_copy_is_a_tree_of_its_own_with_a_local_origin_and_no_credentials(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git on this machine")
    checkout = tmp_path / CHECKOUT_NAME
    checkout.mkdir()
    identity = {"GIT_AUTHOR_NAME": "a-" + "b", "GIT_AUTHOR_EMAIL": "a@b.invalid",
                "GIT_COMMITTER_NAME": "a-" + "b", "GIT_COMMITTER_EMAIL": "a@b.invalid"}
    git_env = os.environ | identity
    subprocess.run([git, "init", "-q"], cwd=checkout, env=git_env, check=True, creationflags=NO_WINDOW)
    (checkout / "notes.txt").write_text("one\n", "utf-8")
    subprocess.run([git, "add", "notes.txt"], cwd=checkout, env=git_env, check=True, creationflags=NO_WINDOW)
    subprocess.run([git, "commit", "-q", "-m", "first"], cwd=checkout, env=git_env, check=True,
                   creationflags=NO_WINDOW)
    subprocess.run([git, "config", "--local", "http.https://github.com/.extraheader", "AUTHORIZATION: x"],
                   cwd=checkout, env=git_env, check=True, creationflags=NO_WINDOW)
    head = subprocess.run([git, "rev-parse", "HEAD"], cwd=checkout, env=git_env, capture_output=True, text=True,
                          check=True, creationflags=NO_WINDOW).stdout.strip()

    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", "utf-8")
    base = tmp_path / "base"
    base.mkdir()
    names = foreign_run.invented_names(REAL_ACCOUNT, REAL_REPO, CHECKOUT_NAME)
    env = foreign_run.child_env(os.environ, home=base / "home", empty_config=empty,
                                path=os.environ["PATH"], os_name=foreign_run.this_os())
    copy = foreign_run.make_copy(checkout, base, names, env)

    assert copy.name == names.folder and copy != checkout
    assert (copy / "notes.txt").read_text("utf-8") == "one\n"
    url = subprocess.run([git, "remote", "get-url", "origin"], cwd=copy, env=env, capture_output=True, text=True,
                         check=True, creationflags=NO_WINDOW).stdout.strip()
    bare = base / "remotes" / names.account / f"{names.repo}.git"
    assert Path(url) == bare and bare.is_dir()
    assert REAL_ACCOUNT not in url and REAL_REPO not in Path(url).name
    copied_head = subprocess.run([git, "rev-parse", "HEAD"], cwd=copy, env=env, capture_output=True, text=True,
                                 check=True, creationflags=NO_WINDOW).stdout.strip()
    assert copied_head == head
    left = subprocess.run([git, "config", "--local", "--get-regexp", r"^http\."], cwd=copy, env=env,
                          capture_output=True, text=True, creationflags=NO_WINDOW)
    assert left.returncode == 1 and left.stdout == "", left.stdout  # the checkout's token header is not copied
