"""The interpreter is found, not assumed (#413, unit 4): no entry point of the repo runs a bare `python` or
`python3`. The entry points are the git hook `hooks/pre-push`, the hook commands of `.claude/settings.json`
and the MCP server of `.mcp.json`; each goes through the shim `hooks/orch-it`, a POSIX sh script that runs
the first interpreter it finds (`ORCH_IT_PYTHON`, then the Windows launcher `py -3`, then `python3`, then
`python` on PATH). Its header says the order. The repo's `tests.tar` (a stray archive named by no file) is gone.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "hooks" / "orch-it"
HOOK = ROOT / "hooks" / "pre-push"
SETTINGS = json.loads((ROOT / ".claude" / "settings.json").read_text("utf-8"))
MCP = json.loads((ROOT / ".mcp.json").read_text("utf-8"))["mcpServers"]["orch-it-research"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BARE = {"python", "python3", "python.exe", "python3.exe"}
SH = shutil.which("sh")

needs_sh = pytest.mark.skipif(SH is None, reason="no POSIX sh on this machine")


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def words(text: str) -> list[str]:
    return shlex.split(text, posix=True)


def bare_python_in(text: str) -> list[str]:
    """Every word of `text` that is a bare interpreter name (its path's last part, e.g. `/usr/bin/python3`)."""
    return [w for w in words(text) if Path(w).name in BARE]


def hook_commands() -> list[str]:
    """Every hook command `.claude/settings.json` wires, in every event."""
    return [h["command"] for entries in SETTINGS["hooks"].values() for entry in entries for h in entry["hooks"]]


def shell_lines(path: Path) -> list[str]:
    return [line for line in path.read_text("utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_no_entry_point_runs_a_bare_python():
    assert not [w for line in shell_lines(HOOK) for w in bare_python_in(line)]
    assert not [w for command in hook_commands() for w in bare_python_in(command)]
    assert not bare_python_in(MCP["command"]) and not [w for a in MCP["args"] for w in bare_python_in(a)]


def test_every_claude_hook_and_the_mcp_server_goes_through_the_shim():
    commands = hook_commands()
    assert commands and all("hooks/orch-it" in c for c in commands), commands
    assert MCP["command"] == "${ORCH_IT_PYTHON}" and MCP["args"] == ["-m", "orch_it", "research-mcp"]
    assert any('/orch-it" -m orch_it hook pre-push "$@"' in line for line in shell_lines(HOOK))
    assert SHIM.is_file()


def test_tests_tar_is_gone_from_the_repo():
    assert subprocess.run(["git", "-C", str(ROOT), "ls-files", "tests.tar"], capture_output=True, text=True,
                          creationflags=NO_WINDOW).stdout == ""
    assert not (ROOT / "tests.tar").exists()


def run_shim(args, env, cwd):
    return subprocess.run([SH, str(SHIM), *args], env=env, cwd=cwd, capture_output=True, text=True,
                          timeout=60, creationflags=NO_WINDOW)


def minimal_env(path_dirs):
    """A temp environment: only the folders given on PATH, nothing of the machine's interpreters; the repo's
    src on PYTHONPATH, so an interpreter that runs the shim can import orch_it."""
    env = {"PATH": os.pathsep.join(str(p) for p in path_dirs), "PYTHONPATH": str(ROOT / "src")}
    for name in ("SYSTEMROOT", "TEMP", "TMP"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


@needs_sh
def test_the_shim_runs_the_interpreter_named_by_orch_it_python(tmp_path):
    env = minimal_env([tmp_path]) | {"ORCH_IT_PYTHON": sys.executable}
    done = run_shim(["-c", "print(1)"], env, tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "1"), done.stderr


@needs_sh
def test_the_shim_finds_an_interpreter_on_path_when_none_is_named(tmp_path):
    env = minimal_env([Path(sys.executable).parent, Path(SH).parent])
    done = run_shim(["-c", "print(1)"], env, tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "1"), done.stderr


@needs_sh
def test_the_shim_refuses_with_127_when_no_interpreter_is_found(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    done = run_shim(["-c", "print(1)"], minimal_env([empty]), tmp_path)
    assert done.returncode == 127 and done.stdout == "", done.stderr
    assert "ORCH_IT_PYTHON" in done.stderr


def fake(path: Path, body: str) -> Path:
    """An executable shell script standing in for an interpreter."""
    path.write_text("#!/bin/sh\n" + body, "utf-8")
    path.chmod(0o755)
    return path


# a fake that cannot import orch_it (its probe fails) and would answer every other call with FAKE
CANNOT_IMPORT = 'case "$*" in\n  *"import orch_it"*) exit 1 ;;\nesac\necho FAKE\n'
# a fake `py` launcher that imports orch_it and echoes the arguments of any other call
LAUNCHER = 'case "$*" in\n  *"import orch_it"*) exit 0 ;;\nesac\necho "py $*"\n'


@needs_sh
def test_the_shim_skips_a_first_candidate_that_cannot_import_orch_it(tmp_path):
    first = fake(tmp_path / "first-candidate", CANNOT_IMPORT)
    env = minimal_env([Path(sys.executable).parent]) | {"ORCH_IT_PYTHON": first.as_posix()}
    done = run_shim(["-c", "print(1)"], env, tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "1"), done.stderr


@needs_sh
def test_the_shim_runs_the_windows_launcher_as_py_3_when_nothing_else_is_on_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake(bin_dir / "py", LAUNCHER)
    done = run_shim(["-c", "print(1)"], minimal_env([bin_dir]), tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "py -3 -c print(1)"), done.stderr


@needs_sh
def test_the_windows_launcher_comes_after_python_and_python3_on_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake(bin_dir / "py", LAUNCHER)
    done = run_shim(["-c", "print(1)"], minimal_env([bin_dir, Path(sys.executable).parent]), tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "1"), done.stderr
