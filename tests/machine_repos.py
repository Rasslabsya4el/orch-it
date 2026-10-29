"""Helpers of the machine place tests: a real local repo with a bare origin and two commits, a
caller work tree checked out at the older one, and a process-liveness check."""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from orch_it.testruns import environment

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GREEN = "def test_adds():\n    assert 1 + 1 == 2\n"
RED = "def test_adds():\n    assert 1 + 1 == 2\n\ndef test_breaks():\n    assert 1 == 2\n"
NEWER = "def test_adds():\n    assert False  # the newer commit differs\n"
SLOW_GREEN = "import time\n\ndef test_adds():\n    time.sleep(3)\n"
SLOW_RED = "import time\n\ndef test_adds():\n    time.sleep(3)\n    assert False\n"


def git(cwd, *args) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
         *args], cwd=cwd, capture_output=True, encoding="utf-8", timeout=60, creationflags=NO_WINDOW)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class Repos:
    """`origin` (bare) holds `old` and `new`; `caller` is a clone checked out at `old`."""

    def __init__(self, tmp_path: Path, old_test: str = GREEN, new_test: str = NEWER):
        seed = tmp_path / "seed"
        seed.mkdir()
        git(seed, "init", "-b", "main")
        (seed / "test_suite.py").write_text(old_test, "utf-8")
        git(seed, "add", ".")
        git(seed, "commit", "-m", "old")
        self.old = git(seed, "rev-parse", "HEAD")
        (seed / "test_suite.py").write_text(new_test, "utf-8")
        git(seed, "commit", "-am", "new")
        self.new = git(seed, "rev-parse", "HEAD")
        self.origin = tmp_path / "origin.git"
        git(tmp_path, "init", "--bare", "-b", "main", str(self.origin))
        git(seed, "push", str(self.origin), "main")
        self.caller = tmp_path / "caller"
        git(tmp_path, "clone", str(self.origin), str(self.caller))
        git(self.caller, "checkout", "--detach", self.old)

    def make_caller_dirty(self):
        (self.caller / "scratch.txt").write_text("not committed", "utf-8")
        (self.caller / "test_suite.py").write_text(NEWER, "utf-8")

    def caller_state(self) -> tuple[str, str]:
        return git(self.caller, "status", "--porcelain"), git(self.caller, "rev-parse", "HEAD")


def prefer_machine(store, machine_place: str):
    """Past runs saying the machine finishes sooner than own, so the place rule offers it first."""

    with store.writer() as writer:
        for n, (place, minutes) in enumerate((("own", 5.0), (machine_place, 0.001))):
            envelope = {"repo": "acme-x/widgets-y", "ticket": None, "role": "script", "session": None,
                        "parent_session": None, "turn": None, "step": "test_run", "run": f"seed{n}",
                        "note": None}
            writer.write(envelope | {"kind": "step.started", "at": "2026-01-01T00:00:00+00:00", "detail": {
                "place": place, "selection": "full", "run_os": environment.this_os(), "bound_minutes": 15}})
            writer.write(envelope | {"kind": "step.ended", "at": "2026-01-01T00:01:00+00:00", "detail": {
                "outcome": "passed", "reason": None, "run_minutes": minutes, "start_minutes": 0.0}})


def sleeping_suite(pidfile: Path) -> str:
    """A suite whose first run records its pid in `pidfile` and sleeps; a later run finds the
    file and passes at once."""

    return textwrap.dedent(f"""
        import os, pathlib, time

        def test_sleeps_once():
            marker = pathlib.Path({str(pidfile)!r})
            if marker.exists():
                return
            marker.write_text(str(os.getpid()))
            time.sleep(120)
        """)


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, 0, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    proc = Path(f"/proc/{pid}/stat")
    try:
        return proc.read_text().rpartition(")")[2].split()[0] != "Z"
    except FileNotFoundError:
        return not Path("/proc/self").exists()  # with /proc mounted, no entry means it just ended
    except (OSError, IndexError):
        return True


def dead_within(pid: int, seconds: float = 15.0) -> bool:
    """Whether the process is gone within `seconds`; one still there is killed, whatever the verdict.
    A process that ends between the last probe and the kill is gone, never an error."""

    deadline = time.monotonic() + seconds
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if not alive(pid):
        return True
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except (ProcessLookupError, OSError):  # Windows says a gone pid with OSError
        return True
    return False
