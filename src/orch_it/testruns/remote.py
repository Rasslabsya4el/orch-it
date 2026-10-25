"""The far side of a machine place (#394 decision 4): what runs on the test machine.

The local side sends this module, `process`, `environment`, `places` and the `orch_it_ids`
plugin as one bundle on the first line of stdin, so the far machine needs nothing but git, Python
and the repo's test dependencies. The second line is the request (JSON). `main` then puts the
checkout folder at the commit (clone when missing, else fetch; `checkout --force --detach`;
`clean -fd`), runs the command once under its bound in the clean environment of this OS, and
prints protocol lines on stdout, each starting `ORCH-IT `:

    START <json>         the command is starting
    END <json>           rc, cut, slept, seconds, log_tail
    REPORT <base64>      the report's bytes, empty when the command wrote none
    SETUP-FAILED <json>  step and message; the command never started

Stdin stays open for the whole run: it is a dead man's switch. When it reaches EOF (the caller
or the connection is gone) the test process tree is killed and `main` exits without a word.
Stdlib only, so it needs no install here.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from orch_it.testruns import places, process

PREFIX = "ORCH-IT "
SETUP_STEP_SECONDS = 600
LOG_TAIL_CHARS = 2000
_COMMIT = re.compile(r"[0-9a-fA-F]{7,64}")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupFailed(Exception):
    def __init__(self, step: str, message: str):
        self.step = step
        super().__init__(message)


def say(kind: str, payload: str) -> None:
    sys.stdout.write(f"{PREFIX}{kind} {payload}\n")
    sys.stdout.flush()


def main(workdir: str) -> None:
    """Serve one request read from stdin, then exit the process (never returns)."""

    gone = threading.Event()
    code = 0
    try:
        request = json.loads(sys.stdin.readline())
        threading.Thread(target=_watch_stdin, args=(gone,), daemon=True).start()
        try:
            checkout = _setup(request, gone)
        except SetupFailed as e:
            say("SETUP-FAILED", json.dumps({"step": e.step, "message": str(e)[-LOG_TAIL_CHARS:]}))
        else:
            if not gone.is_set():
                _run(request, Path(workdir), checkout, gone)
    except BaseException:
        code = 1
        traceback.print_exc()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)  # a thread blocked on stdin must not hold the interpreter's shutdown


def _watch_stdin(gone: threading.Event) -> None:
    try:
        while sys.stdin.readline():
            pass
    except (OSError, ValueError):
        pass
    gone.set()


def _git(args: list[str], *, cwd: str | None, step: str) -> str:
    env = os.environ | {"GIT_TERMINAL_PROMPT": "0"}
    try:
        # stdin is ours (the dead man's switch): git gets none of it
        done = subprocess.run(["git", *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, encoding="utf-8", errors="replace",
                              timeout=SETUP_STEP_SECONDS, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SetupFailed(step, f"git {args[0]}: {e}") from None
    if done.returncode != 0:
        raise SetupFailed(step, f"git {args[0]} failed ({done.returncode}): {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip()


def _setup(request: dict, gone: threading.Event) -> str:
    """Put the checkout folder at the requested commit; return the folder."""

    commit = request["commit"]
    if not _COMMIT.fullmatch(commit):
        raise SetupFailed("commit", f"not a commit id: {commit!r}")
    checkout = os.path.abspath(os.path.expanduser(request["checkout"]))
    if not os.path.isdir(os.path.join(checkout, ".git")):
        if not request.get("origin"):
            raise SetupFailed("clone", "the folder is no clone and no origin url is known")
        os.makedirs(os.path.dirname(checkout), exist_ok=True)
        _git(["clone", "--no-checkout", "--", request["origin"], checkout], cwd=None, step="clone")
    else:
        _git(["fetch", "origin"], cwd=checkout, step="fetch")
    if gone.is_set():
        return checkout
    try:
        _git(["cat-file", "-e", commit + "^{commit}"], cwd=checkout, step="commit")
    except SetupFailed:
        _git(["fetch", "origin", commit], cwd=checkout, step="fetch")  # a commit no branch holds
        _git(["cat-file", "-e", commit + "^{commit}"], cwd=checkout, step="commit")
    _git(["checkout", "--force", "--detach", commit], cwd=checkout, step="checkout")
    _git(["clean", "-fd"], cwd=checkout, step="clean")
    return checkout


def _run(request: dict, workdir: Path, checkout: str, gone: threading.Event) -> None:
    report = workdir / "junit.xml"
    ids_file = None
    if request.get("ids") is not None:
        ids_file = workdir / "ids.txt"
        ids_file.write_text("".join(f"{i}\n" for i in request["ids"]), "utf-8")
    argv = places.fill(tuple(request["command"]), {
        "python": sys.executable, "report": str(report), "ids_file": str(ids_file or ""),
        "work_tree": checkout})
    env = places.test_process_env(request["test_env"], str(ids_file) if ids_file else None,
                                  str(places.PLUGIN_FOLDER))
    say("START", json.dumps({"checkout": checkout, "python": sys.executable}))
    try:
        done = process.run_bounded(argv, cwd=Path(checkout), env=env,
                                   bound_seconds=request["bound_seconds"], abort=gone)
    except process.CommandMissing as e:
        done = process.Finished(None, False, False, 0.0, str(e))
    if gone.is_set():
        return
    say("END", json.dumps({"rc": done.rc, "cut": done.cut, "slept": done.slept, "seconds": done.seconds,
                           "log_tail": done.log_tail[-LOG_TAIL_CHARS:]}))
    say("REPORT", base64.b64encode(report.read_bytes()).decode("ascii") if report.exists() else "")
