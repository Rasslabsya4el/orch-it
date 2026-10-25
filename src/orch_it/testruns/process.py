"""One test command run under its bound (#394 decision 2, the `cut` row).

`run_bounded` starts the command with no console window, waits until it exits or its bound
passes, and then kills its whole process tree either way, so no process of the run is left: on
Windows every process of the run sits in one job object (the command starts suspended, joins
the job, then resumes); elsewhere the command leads its own process group (a process that
starts its own session escapes it). The bound passes when either the monotonic clock or the
wall clock says so: a wall clock stepping back never stretches it, and a machine that sleeps
through it still cuts the run. A jump of the wall clock between two polls longer than
`SLEEP_GAP_SECONDS` that carries the run past its bound marks it `slept` (a short sleep long
before the bound does not). The command's output is read through a pipe and only its last
`LOG_TAIL_BYTES` are kept.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

POLL_SECONDS = 0.5
SLEEP_GAP_SECONDS = 60.0
KILL_WAIT_SECONDS = 30.0
LOG_TAIL_BYTES = 8192


@dataclass(frozen=True)
class Finished:
    rc: int | None  # None: killed at the bound
    cut: bool
    slept: bool
    seconds: float
    log_tail: str = ""


class CommandMissing(Exception):
    """The command could not be started (not found, not executable, not a valid argument)."""


def run_bounded(argv: list[str], *, cwd: Path, env: dict[str, str] | None, bound_seconds: float,
                wall=time.time, poll_seconds: float = POLL_SECONDS,
                abort: threading.Event | None = None) -> Finished:
    """Run `argv` until it exits, its bound passes, or `abort` is set (then it is killed like a
    cut run: a machine place sets it when its caller is gone)."""

    try:
        tree = _Tree.start(argv, cwd, env)
    except (OSError, ValueError) as e:
        raise CommandMissing(f"{argv[0] if argv else ''}: {e}") from None
    tail = _Tail(tree.process.stdout)
    started_wall = last = wall()
    started_mono = time.monotonic()
    deadline = started_wall + bound_seconds
    slept = cut = False
    try:
        while True:
            try:
                tree.process.wait(timeout=poll_seconds)
                break
            except subprocess.TimeoutExpired:
                pass
            if abort is not None and abort.is_set():
                cut = True
                break
            now = wall()
            if now - last > poll_seconds + SLEEP_GAP_SECONDS and last < deadline <= now:
                slept = True  # the machine slept across the bound
            last = now
            if now >= deadline or time.monotonic() - started_mono >= bound_seconds:
                cut = True
                break
    finally:
        tree.kill()
        tail.join()
    seconds = max(time.monotonic() - started_mono, wall() - started_wall, 0.0)
    return Finished(None if cut else tree.process.returncode, cut, slept, seconds, tail.text())


class _Tail:
    """Reads a pipe to its end on a thread of its own, keeping its last `LOG_TAIL_BYTES`."""

    def __init__(self, pipe):
        self.chunks: deque[bytes] = deque()
        self.size = 0
        self.pipe = pipe
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        try:
            while chunk := self.pipe.read1(65536):
                self.chunks.append(chunk)
                self.size += len(chunk)
                while self.size - len(self.chunks[0]) >= LOG_TAIL_BYTES:
                    self.size -= len(self.chunks.popleft())
        except (OSError, ValueError):
            pass

    def join(self) -> None:
        self.thread.join(timeout=KILL_WAIT_SECONDS)
        self.pipe.close()

    def text(self) -> str:
        return b"".join(self.chunks)[-LOG_TAIL_BYTES:].decode("utf-8", "replace")


class _Tree:
    """A started command and every process it starts."""

    def __init__(self, process: subprocess.Popen, job=None):
        self.process = process
        self.job = job

    @classmethod
    def start(cls, argv, cwd, env) -> _Tree:
        common = {"cwd": cwd, "env": env, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                  "stderr": subprocess.STDOUT}
        if sys.platform != "win32":
            return cls(subprocess.Popen(argv, start_new_session=True, **common))
        process = subprocess.Popen(argv, creationflags=_win.CREATE_SUSPENDED | _win.CREATE_NO_WINDOW,
                                   **common)
        try:
            job = _win.job_for(process)
            _win.resume(process)
        except BaseException:  # never leave a suspended process nobody kills
            process.kill()
            process.wait(timeout=KILL_WAIT_SECONDS)
            raise
        return cls(process, job)

    def kill(self) -> None:
        if sys.platform == "win32":
            _win.kill(self.process, self.job)
        else:
            try:
                os.killpg(self.process.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            self.process.wait(timeout=KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=KILL_WAIT_SECONDS)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Win:
        CREATE_SUSPENDED = 0x00000004
        CREATE_NO_WINDOW = 0x08000000
        KILL_ON_JOB_CLOSE = 0x00002000
        EXTENDED_LIMIT_INFORMATION = 9

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class _Extended(ctypes.Structure):
            pass

        _Extended._fields_ = [("BasicLimitInformation", _Basic),
                              ("IoInfo", ctypes.c_ulonglong * 6),
                              ("ProcessMemoryLimit", ctypes.c_size_t),
                              ("JobMemoryLimit", ctypes.c_size_t),
                              ("PeakProcessMemoryUsed", ctypes.c_size_t),
                              ("PeakJobMemoryUsed", ctypes.c_size_t)]

        def __init__(self):
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.ntdll = ctypes.WinDLL("ntdll")
            k = self.kernel32
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                  wintypes.DWORD]
            k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            self.ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

        def job_for(self, process: subprocess.Popen):
            """A job object holding `process` (still suspended), or None when Windows would not
            make one; then `kill` falls back to `taskkill /T`."""

            k = self.kernel32
            job = k.CreateJobObjectW(None, None)
            if not job:
                return None
            info = self._Extended()
            info.BasicLimitInformation.LimitFlags = self.KILL_ON_JOB_CLOSE
            if not (k.SetInformationJobObject(job, self.EXTENDED_LIMIT_INFORMATION,
                                              ctypes.byref(info), ctypes.sizeof(info))
                    and k.AssignProcessToJobObject(job, int(process._handle))):
                k.CloseHandle(job)
                return None
            return job

        def resume(self, process: subprocess.Popen) -> None:
            self.ntdll.NtResumeProcess(int(process._handle))

        def kill(self, process: subprocess.Popen, job) -> None:
            if job is not None:
                self.kernel32.TerminateJobObject(job, 1)
                self.kernel32.CloseHandle(job)
                return
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, timeout=KILL_WAIT_SECONDS,
                           creationflags=self.CREATE_NO_WINDOW)

    _win = _Win()
