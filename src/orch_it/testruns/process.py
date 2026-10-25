"""One test command run under its bound (#394 decision 2, the `cut` row).

`run_bounded` starts the command with no console window, waits until it exits or its bound
passes, and then kills its whole process tree either way, so no process of the run is left: on
Windows every process of the run sits in one job object (the command starts suspended, joins
the job, then resumes); elsewhere the command leads its own process group. The bound is wall
time, so a machine that sleeps through it still cuts the run; a jump of the wall clock between
two polls longer than `SLEEP_GAP_SECONDS` that carries the run past its bound marks it `slept`
(a short sleep long before the bound does not).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

POLL_SECONDS = 0.5
SLEEP_GAP_SECONDS = 60.0
KILL_WAIT_SECONDS = 30.0


@dataclass(frozen=True)
class Finished:
    rc: int | None  # None: killed at the bound
    cut: bool
    slept: bool
    seconds: float


class CommandMissing(Exception):
    """The command's program could not be started (not found, not executable)."""


def run_bounded(argv: list[str], *, cwd: Path, env: dict[str, str], bound_seconds: float,
                log_path: Path, wall=time.time, poll_seconds: float = POLL_SECONDS) -> Finished:
    with open(log_path, "wb") as log:
        try:
            tree = _Tree.start(argv, cwd, env, log)
        except OSError as e:
            raise CommandMissing(f"{argv[0]}: {e}") from None
        started = last = wall()
        deadline = started + bound_seconds
        slept = cut = False
        try:
            while True:
                try:
                    tree.process.wait(timeout=poll_seconds)
                    break
                except subprocess.TimeoutExpired:
                    pass
                now = wall()
                if now - last > poll_seconds + SLEEP_GAP_SECONDS and last < deadline <= now:
                    slept = True  # the machine slept across the bound
                last = now
                if now >= deadline:
                    cut = True
                    break
        finally:
            tree.kill()
        seconds = max(wall() - started, 0.0)
    return Finished(None if cut else tree.process.returncode, cut, slept, seconds)


class _Tree:
    """A started command and every process it starts."""

    def __init__(self, process: subprocess.Popen, job=None):
        self.process = process
        self.job = job

    @classmethod
    def start(cls, argv, cwd, env, log) -> _Tree:
        common = {"cwd": cwd, "env": env, "stdin": subprocess.DEVNULL, "stdout": log,
                  "stderr": subprocess.STDOUT}
        if sys.platform != "win32":
            return cls(subprocess.Popen(argv, start_new_session=True, **common))
        process = subprocess.Popen(argv, creationflags=_win.CREATE_SUSPENDED | _win.CREATE_NO_WINDOW,
                                   **common)
        job = _win.job_for(process)
        _win.resume(process)
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
