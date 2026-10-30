"""The workers of a test run at a PC place (#414 item 11): a suite runs on all the cores free on the machine.

K is the machine's logical cores minus the cores its current load shows busy, at least 1, and at most the cap
setting (`own_workers_max` for the own place, `workers_max` of a machine). A pytest command that can take
`-n K` (pytest-xdist importable by this interpreter, and the command is `<this interpreter> -m pytest`) gets
`-n K` right after the `pytest` token, once. A command that already carries `-n` / `--numprocesses` or
switches xdist off (`-p no:xdist`) is left as it is (note `workers_in_command`), a PC without xdist, or a
command of another interpreter or a bare `pytest`, runs one process (note `workers_unavailable`), and a command
that is not pytest is left as it is with no workers at all.

Stdlib only: the far side of a machine place runs this module from the bundle (`machine.BUNDLE`), and so
the count is the one of the machine the suite runs on, never of the caller. `psutil`, when the machine has it,
reads the load; else the one-minute load average does; a machine with neither counts as idle.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from dataclasses import dataclass

NOTE_UNAVAILABLE = "workers_unavailable"  # a pytest command, but no xdist on the PC: one process
NOTE_IN_COMMAND = "workers_in_command"  # the command carries its own `-n` / `--numprocesses`
SAMPLE_SECONDS = 0.2  # how long psutil watches the cores to read their load
_PYTEST_EXES = ("pytest", "py.test")
PYTHON_PLACEHOLDER = "{python}"  # filled with `sys.executable` when the run starts (`places.fill`)
_N_JOINED = re.compile(r"-n=?(\d+|auto|logical)")  # `-n4`, `-n=4`, `-nauto`: exactly, never any `-n...` flag
_XDIST_OFF = re.compile(r"(?:-p)?no:xdist(?:\.plugin)?")


@dataclass(frozen=True)
class Plan:
    """`command` as the run starts it, `workers` the number it runs on (None: not a pytest command, or the
    command carries a count that is not a number), `note` the word that says why K is not the free cores."""

    command: tuple[str, ...]
    workers: int | None
    note: str | None = None


def system_load() -> tuple[int | None, float | None]:
    """(logical cores of this machine, cores busy now), each None when it cannot be read."""

    try:
        cores = len(os.sched_getaffinity(0))  # a process pinned to fewer cores has fewer
    except (AttributeError, OSError):
        cores = os.cpu_count()
    if not cores:
        return None, None
    try:
        import psutil

        return cores, psutil.cpu_percent(interval=SAMPLE_SECONDS) / 100 * cores
    except Exception:  # not installed on the machine, or the OS would not say
        pass
    try:
        return cores, os.getloadavg()[0]
    except (AttributeError, OSError):
        return cores, None


def free_cores(load=None) -> int:
    """The cores free now: cores minus those busy (rounded to a whole core), at least 1. `load` reads
    (cores, busy); default `system_load`."""

    cores, busy = (load or system_load)()
    if not cores:
        return 1
    return max(1, cores - int((busy or 0.0) + 0.5))


def xdist_importable() -> bool:
    """Whether this interpreter, the one that runs the suite, can import pytest-xdist."""

    try:
        return importlib.util.find_spec("xdist") is not None
    except (ImportError, ValueError):
        return False


def _pytest_at(command: tuple[str, ...]) -> int | None:
    """The index of the `pytest` token of a pytest command (`pytest ...`, `<python> -m pytest ...`), else None."""

    for i, arg in enumerate(command):
        if i == 0 and os.path.basename(arg).lower().removesuffix(".exe") in _PYTEST_EXES:
            return i
        if arg == "pytest" and command[i - 1:i] == ("-m",):
            return i
    return None


def _same_interpreter(command: tuple[str, ...], at: int) -> bool:
    """Whether `command` is `<this interpreter> -m pytest ...`: the interpreter the xdist probe asked. A bare
    `pytest` executable or another interpreter may have another set of packages."""

    if at != 2:  # `<python> -m pytest`: the interpreter is the token before `-m`
        return False
    if command[0] == PYTHON_PLACEHOLDER:
        return True
    return os.path.normcase(os.path.abspath(command[0])) == os.path.normcase(os.path.abspath(sys.executable))


def _carried(command: tuple[str, ...]) -> tuple[bool, int | None]:
    """(whether the command carries its workers or switches xdist off, the number when it is one)."""

    for i, arg in enumerate(command):
        after = command[i + 1] if arg == "-p" and i + 1 < len(command) else ""
        if _XDIST_OFF.fullmatch(after) or arg.startswith("-p") and _XDIST_OFF.fullmatch(arg):
            return True, 1  # `-p no:xdist`, `-pno:xdist`: the plugin is off, so one process
        if arg in ("-n", "--numprocesses"):
            value = command[i + 1] if i + 1 < len(command) else ""
        elif arg.startswith("--numprocesses="):
            value = arg.split("=", 1)[1]
        elif _N_JOINED.fullmatch(arg):
            value = arg[2:].lstrip("=")
        else:
            continue
        return True, int(value) if value.isdigit() else None
    return False, None


def plan(command: tuple[str, ...], *, cap: int | None = None, load=None, xdist=None) -> Plan:
    """The run of `command` on this machine. `cap`: the most workers (None: all the free cores). `load` and
    `xdist` are the readers of the machine (defaults `system_load`, `xdist_importable`), replaced by a test."""

    at = _pytest_at(command)
    if at is None:
        return Plan(command, None)
    carried, count = _carried(command)
    if carried:
        return Plan(command, count, NOTE_IN_COMMAND)
    if not _same_interpreter(command, at):
        return Plan(command, 1, NOTE_UNAVAILABLE)
    k = free_cores(load)
    if cap is not None:
        k = max(1, min(k, cap))
    if k == 1:
        return Plan(command, 1)
    if not (xdist or xdist_importable)():
        return Plan(command, 1, NOTE_UNAVAILABLE)
    return Plan((*command[:at + 1], "-n", str(k), *command[at + 1:]), k)
