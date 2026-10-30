"""The foreign run (#414): the orch-it suite and the layer's setup commands, run in a copy of the checkout that is not the
owner's setup. The copy sits in a folder with another name, its origin is a local bare repo under an invented account
and repo, and the child gets the clean environment (the glossary's term): the OS's base variables only, a fresh home,
git's configuration read from an empty file, no git identity, no GitHub variable, no token. The one switch of the
suite that the job sets (ORCH_IT_REQUIRE_SSHD, as the Linux pytest step does) is passed on to the child.

Bin rule (POSIX; macOS is POSIX too, and keeps the same rule): the child's PATH is one bin folder with a link to every
executable file of the normal PATH, except the names `excluded_from_bin` excludes: `python`, `python2` and any
`python*` other than `python3`. So python3 is the only Python the child can run, while git, sh and bash (git runs its
hooks through sh; the shell tests look for bash) and every other tool stay reachable.

Pure standard library: it runs before any install, with the interpreter the job set up.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE_SWITCHES = ("ORCH_IT_REQUIRE_SSHD",)  # set by a job for the suite (the Linux pytest step sets it); passed on
# the live tests read GitHub's own runs with GH_TOKEN and gh, which this setup does not hold; the nightly windows job runs them
NIGHTLY_SELECTION = "not live"
# the OS's base variables a test process gets; mirrors `clean_environment` (src/orch_it/testruns/environment.py).
# HOMEDRIVE and HOMEPATH are left out: they name the runner's real home, and USERPROFILE is set to the fresh one.
BASE_NAMES = {
    "nt": ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
           "USERPROFILE", "APPDATA", "LOCALAPPDATA"),
    "posix": ("PATH", "HOME", "TMPDIR"),
}
WINDOWS_HOME_NAMES = ("PATH", "HOME", "USERPROFILE")


class ForeignRunError(Exception):
    """A step the foreign run cannot make: the run stops with the reason, never half of it."""


@dataclass(frozen=True)
class Names:
    folder: str
    account: str
    repo: str


def this_os() -> str:
    """This machine's key in BASE_NAMES: `nt` on Windows, `posix` elsewhere."""

    return "nt" if os.name == "nt" else "posix"


def _random_part() -> str:
    return secrets.token_hex(4)


def invented_names(real_account: str, real_repo: str, checkout_name: str,
                   draw: Callable[[], str] = _random_part) -> Names:
    """Random names for the copy's folder and its origin: the folder is neither the real repo's name nor the
    checkout's, the origin's account and repo are not the real ones, and the folder is not the origin's repo name
    either (a comparison of the two inside the copy must come out unequal). Names compare case-insensitively, as
    GitHub's do; a clashing draw is drawn again."""

    taken = {real_repo.casefold(), checkout_name.casefold()}
    while True:
        names = Names(folder=f"tree-{draw()}", account=f"acct-{draw()}", repo=f"repo-{draw()}")
        if (names.folder.casefold() not in taken and names.folder.casefold() != names.repo.casefold()
                and names.account.casefold() != real_account.casefold()
                and names.repo.casefold() != real_repo.casefold()):
            return names


def _owner_and_repo(text: str) -> tuple[str, str]:
    parts = [part for part in re.split(r"[/:\\]", text.strip().removesuffix(".git")) if part]
    return (parts[-2], parts[-1]) if len(parts) >= 2 else ("", "")


def real_names(checkout: Path, environ: Mapping[str, str]) -> tuple[str, str]:
    """The real account and repo: GITHUB_REPOSITORY on GitHub, else the checkout's origin; empty when neither."""

    full = environ.get("GITHUB_REPOSITORY", "")
    if not full:
        done = subprocess.run(["git", "-C", str(checkout), "remote", "get-url", "origin"],
                              capture_output=True, text=True)
        if done.returncode == 0:
            return _owner_and_repo(done.stdout)
    return _owner_and_repo(full)


def child_env(source: Mapping[str, str], *, home: Path, empty_config: Path, path: str, os_name: str) -> dict[str, str]:
    """The child's clean environment: the base variables of `os_name` found in `source`, then the given PATH, a fresh
    home (and USERPROFILE on Windows), and git's configuration read from `empty_config` alone. Nothing else of
    `source` reaches the child: no identity, no GitHub variable, no token, no CI flag."""

    if os_name == "nt":
        upper = {name.upper(): name for name in source}
        env = {upper[name.upper()]: str(source[upper[name.upper()]])
               for name in BASE_NAMES["nt"] if name.upper() in upper}
        env = {key: value for key, value in env.items() if key.upper() not in WINDOWS_HOME_NAMES}
    else:
        env = {name: str(source[name]) for name in BASE_NAMES[os_name] if name in source}
    env["PATH"] = path
    env["HOME"] = str(home)
    if os_name == "nt":
        env["USERPROFILE"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["GIT_CONFIG_GLOBAL"] = str(empty_config)
    env["GIT_CONFIG_SYSTEM"] = str(empty_config)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def suite_switches(env: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    """The child's environment with the suite's own switches of `environ` copied in (only those the job set)."""

    return dict(env) | {name: environ[name] for name in SUITE_SWITCHES if name in environ}


def selection(nightly: bool, marks_left_out: str) -> str:
    """The pytest `-m` expression of the run: the change run's marks (the workflow's `MARKS_LEFT_OUT_OF_CHANGE_RUNS`),
    or on the nightly everything but the live tests (`NIGHTLY_SELECTION`)."""

    return NIGHTLY_SELECTION if nightly else marks_left_out


def excluded_from_bin(name: str) -> bool:
    """The bin rule: no `python`, no `python2`, no `python*` other than `python3` in the child's PATH. macOS keeps
    this rule as well: its foreign job runs on the same POSIX PATH as Linux, and a looser rule would let a bare
    `python` through there."""

    return name.startswith("python") and name != "python3"


def make_bin(bin_dir: Path, source_path: str) -> None:
    """POSIX, the bin rule (`excluded_from_bin`): `bin_dir` holds a link to every executable file of the folders of
    `source_path`, in PATH order, so the first file of a name wins as it does on the PATH; the excluded names are not
    linked. Calling it again links what appeared since (a pip install puts its scripts in python3's folder)."""

    bin_dir.mkdir(exist_ok=True)
    for folder in source_path.split(os.pathsep):
        if not folder or not Path(folder).is_dir():
            continue
        for entry in sorted(Path(folder).iterdir()):
            if excluded_from_bin(entry.name) or not entry.is_file() or not os.access(entry, os.X_OK):
                continue
            _link(bin_dir / entry.name, entry)


def _link(link: Path, target: Path) -> None:
    if link.is_symlink() or link.exists():
        return
    os.symlink(target, link)


def _git(args: list[str], cwd: Path, env: Mapping[str, str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=check)


def make_copy(checkout: Path, base: Path, names: Names, env: Mapping[str, str]) -> Path:
    """The checkout's tree with its history, in `base/<folder>`: its origin is a local bare repo of the invented
    account and repo, the copy's HEAD is pushed there, and the checkout's http headers (the token of the checkout
    step) are left out of the copy."""

    if (checkout / ".git").is_file():
        raise ForeignRunError("the checkout's .git is a file (a worktree): the foreign run needs a full clone")
    copy = base / names.folder
    shutil.copytree(checkout, copy, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "junit*.xml"))
    listed = _git(["config", "--local", "--name-only", "--get-regexp", r"^http\."], copy, env, check=False)
    for key in listed.stdout.split():
        _git(["config", "--local", "--unset-all", key], copy, env)
    bare = base / "remotes" / names.account / f"{names.repo}.git"
    bare.parent.mkdir(parents=True)
    _git(["init", "--bare", "--quiet", str(bare)], base, env)
    _git(["remote", "remove", "origin"], copy, env, check=False)
    _git(["remote", "add", "origin", str(bare)], copy, env)
    _git(["push", "--quiet", "origin", "HEAD:refs/heads/main"], copy, env)
    return copy


def _step(argv: list[str], cwd: Path, env: Mapping[str, str], what: str) -> None:
    done = subprocess.run(argv, cwd=cwd, env=dict(env))
    if done.returncode != 0:
        raise ForeignRunError(f"{what} exited {done.returncode}")


def _make_writable(func, path, _exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def run(checkout: Path, environ: Mapping[str, str]) -> int:
    """The whole foreign run of `checkout`: the copy, its origin, the setup commands, the suite. Returns the suite's
    exit code; the suite's JUnit report is written to the checkout's `junit.xml`."""

    if "MARKS_LEFT_OUT_OF_CHANGE_RUNS" not in environ:
        raise ForeignRunError("MARKS_LEFT_OUT_OF_CHANGE_RUNS is not set (the workflow's env)")
    account, repo = real_names(checkout, environ)
    names = invented_names(account, repo, checkout.name)
    base = Path(tempfile.mkdtemp(prefix="foreign-run-"))
    try:
        home = base / "home"
        home.mkdir()
        empty = base / "empty.gitconfig"
        empty.write_text("", "utf-8")
        source_path = environ.get("PATH", "")
        bin_dir = None
        if os.name == "nt":
            path, python = source_path, sys.executable
        else:
            # python3 is looked up on the PATH, not taken as sys.executable: the run proves that python3, found the way
            # the runner finds it, is enough for the setup commands and the suite
            if shutil.which("python3", path=source_path) is None:
                raise ForeignRunError("no python3 on PATH")
            bin_dir = base / "bin"
            make_bin(bin_dir, source_path)
            path, python = str(bin_dir), str(bin_dir / "python3")
        env = suite_switches(child_env(environ, home=home, empty_config=empty, path=path, os_name=this_os()), environ)
        copy = make_copy(checkout, base, names, env)
        print(f"foreign run: the copy is in a folder of its own; its origin is {names.account}/{names.repo}, invented")
        _step([python, "-m", "pip", "install", "--disable-pip-version-check", "-e", ".[test]"], copy, env,
              "the setup command `pip install -e .[test]`")
        if bin_dir is not None:
            make_bin(bin_dir, source_path)  # pip put its scripts in python3's folder: link them too
        _step([python, "-m", "orch_it", "guards", "install", "--work-tree", str(copy)], copy, env,
              "the setup command `guards install`")
        nightly = environ.get("NIGHTLY") == "true"
        kind = "nightly" if nightly else "per_change"
        bound = tomllib.loads((copy / "pyproject.toml").read_text("utf-8"))["tool"]["orch_it"]["tests"][
            f"{kind}_timeout_seconds"]
        marks = selection(nightly, environ["MARKS_LEFT_OUT_OF_CHANGE_RUNS"])
        args = [python, "-m", "pytest", "-q", f"--timeout={bound}", f"--junitxml={checkout / 'junit.xml'}",
                "-m", marks]
        print(f"foreign run: pytest ({kind} run, marks `{marks}`, each test bound to {bound} seconds)")
        return subprocess.run(args, cwd=copy, env=env).returncode
    finally:
        shutil.rmtree(base, onerror=_make_writable)


def main() -> int:
    try:
        return run(ROOT, os.environ)
    except ForeignRunError as error:
        print(f"foreign run: stopped: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
