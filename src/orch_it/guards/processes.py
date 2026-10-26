"""Which processes a session owns, and stopping an own one with its whole tree (#395 decision 3).

A process is own when one of three facts proves it: its environment carries this session's id
(the setting `session_env_var`, default `CLAUDE_CODE_SESSION_ID`, which Claude Code gives every
shell and child of a session), or its parent chain — each parent older than its child, so a
reused pid never counts — reaches the session's agent process, or the store recorded a `process`
this session started with that pid and start time. Nothing proven is foreign.

The ownership rule is pure over a `Facts` reading of the process tree; the psutil reading is the
one adapter behind `Facts`, used on Windows, Linux and macOS (#378). Stopping ends an own
process and its whole tree, children first, so no wrapper is left holding a pipe.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_SESSION_ENV_VAR = "CLAUDE_CODE_SESSION_ID"
# a parent chain longer than this is not walked further (a cycle or a pathological tree)
MAX_CHAIN = 200


@dataclass(frozen=True)
class Proc:
    """One process as the ownership rule needs it: its pid, its start time (the clock the OS
    stamps it with), its parent's pid, and this session's id read from its environment (None
    when absent or unreadable)."""

    pid: int
    create_time: float
    ppid: int | None
    env_session: str | None


def is_own(pid: int, *, facts, session_id: str | None, agent_pid: int | None,
           recorded: set[tuple[int, float]]) -> bool:
    """Whether the live process `pid` belongs to this session. `facts.get(pid)` returns a
    `Proc` or None. `recorded` is the (pid, start time) of every `process` this session started,
    read from the store. A pid the facts cannot read is foreign (nothing proven)."""

    start = facts.get(pid)
    if start is None:
        return False
    if session_id is not None and start.env_session == session_id:
        return True
    if (pid, start.create_time) in recorded:
        return True
    return _chain_reaches(start, facts, agent_pid)


def _chain_reaches(start: Proc, facts, agent_pid: int | None) -> bool:
    """Whether `start`'s parent chain reaches `agent_pid`, each parent older than its child so a
    reused pid breaks the link; stops on a cycle or a missing parent."""

    if agent_pid is None:
        return False
    current = start
    seen: set[int] = set()
    for _ in range(MAX_CHAIN):
        if current.pid == agent_pid:
            return True
        if current.ppid is None or current.pid in seen:
            return False
        seen.add(current.pid)
        parent = facts.get(current.ppid)
        if parent is None or parent.create_time > current.create_time:
            return False  # no parent, or a reused pid stamped later than its supposed child
        current = parent
    return False


# --- judging a stop command ------------------------------------------------------------------

STOP_REFUSALS = ("foreign_process", "unresolved")


@dataclass(frozen=True)
class StopRefusal:
    """Why a stop command is refused. `reason`: `foreign_process` (a process it would stop is
    not own), `unresolved` (a target it could not read, so ownership cannot be proven);
    `pid`: the first foreign process, or None; `rule_id`: the stop that triggered."""

    reason: str
    pid: int | None
    rule_id: str


def judge_stop(stop, *, facts, session_id: str | None, agent_pid: int | None,
               recorded: set[tuple[int, float]]) -> StopRefusal | None:
    """None when every process the stop would end is own; a `StopRefusal` when any is foreign or
    a target cannot be read. A name or pattern target is resolved against the live process list,
    and one foreign match refuses the whole command; a tree stop judges every descendant too.
    A target that matches no live process is nothing to stop, not a refusal."""

    if getattr(stop, "opaque", False):
        return StopRefusal("unresolved", None, stop.rule_id)
    targets: set[int] = set()
    for pid in stop.pids:
        if facts.get(pid) is not None:
            targets.add(pid)
    for matcher in stop.matchers:
        targets |= _matching_pids(matcher, facts)
    if getattr(stop, "tree", False):
        targets = _with_descendants(targets, facts)
    for pid in sorted(targets):
        if not is_own(pid, facts=facts, session_id=session_id, agent_pid=agent_pid,
                      recorded=recorded):
            return StopRefusal("foreign_process", pid, stop.rule_id)
    return None


def _matching_pids(matcher, facts) -> set[int]:
    import fnmatch
    import re

    out: set[int] = set()
    try:
        live = facts.live_pids()
    except Exception:
        return out
    for pid in live:
        name = facts.name(pid)
        if matcher.kind == "name_glob":
            if name is None:
                continue
            if (name.casefold() == matcher.text.casefold() if matcher.exact
                    else fnmatch.fnmatch(name.casefold(), matcher.text.casefold())):
                out.add(pid)
        else:
            text = (facts.cmdline(pid) if matcher.kind == "cmdline_regex" else name) or ""
            try:
                pattern = (r"\A(?:" + matcher.text + r")\Z") if matcher.exact else matcher.text
                if re.search(pattern, text):
                    out.add(pid)
            except re.error:
                continue  # an invalid pattern matches nothing (as the real tool would error out)
    return out


def _with_descendants(pids: set[int], facts) -> set[int]:
    """`pids` plus every live descendant of each, read from the live tree's parent links."""

    try:
        live = facts.live_pids()
    except Exception:
        return set(pids)
    children: dict[int, list[int]] = {}
    for pid in live:
        proc = facts.get(pid)
        if proc is not None and proc.ppid is not None:
            children.setdefault(proc.ppid, []).append(pid)
    out: set[int] = set()
    stack = list(pids)
    while stack:
        pid = stack.pop()
        if pid in out:
            continue
        out.add(pid)
        stack += children.get(pid, [])
    return out


# --- the psutil reading ----------------------------------------------------------------------

class PsutilFacts:
    """The one adapter behind `Facts`: the live process tree read through psutil (#378). A
    process that ends or cannot be read between calls reads as absent (None), never an error."""

    def __init__(self, session_env_var: str = DEFAULT_SESSION_ENV_VAR):
        import psutil

        self._psutil = psutil
        self._var = session_env_var

    def get(self, pid: int) -> Proc | None:
        psutil = self._psutil
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                create_time = p.create_time()
                try:
                    ppid = p.ppid()
                except (psutil.Error, OSError):
                    ppid = None
            env_session = self._env_session(p)
        except (psutil.Error, OSError, ValueError):
            return None
        return Proc(pid=pid, create_time=create_time, ppid=ppid, env_session=env_session)

    def _env_session(self, p) -> str | None:
        try:
            return p.environ().get(self._var)
        except (self._psutil.Error, OSError, ValueError, NotImplementedError):
            return None  # another user's environment, or a platform that will not give it

    def live_pids(self) -> list[int]:
        return self._psutil.pids()

    def name(self, pid: int) -> str | None:
        try:
            return self._psutil.Process(pid).name()
        except (self._psutil.Error, OSError, ValueError):
            return None

    def cmdline(self, pid: int) -> str | None:
        try:
            return " ".join(self._psutil.Process(pid).cmdline())
        except (self._psutil.Error, OSError, ValueError):
            return None


KILL_WAIT_SECONDS = 10.0


def stop_tree(pid: int, *, timeout: float = KILL_WAIT_SECONDS) -> int:
    """End the process `pid` and its whole tree, children first so a wrapper is never left
    holding the pipes of a child that outlives it (P521, P905). Terminate, wait, then kill any
    survivor; returns how many processes were ended. A process already gone is no error."""

    import psutil

    try:
        root = psutil.Process(pid)
    except (psutil.Error, OSError, ValueError):
        return 0
    try:
        descendants = root.children(recursive=True)
    except (psutil.Error, OSError):
        descendants = []
    # deepest first: a child is asked to end before its parent
    ordered = sorted(descendants, key=_depth, reverse=True) + [root]
    for proc in ordered:
        try:
            proc.terminate()
        except (psutil.Error, OSError):
            pass
    gone, alive = psutil.wait_procs(ordered, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.Error, OSError):
            pass
    psutil.wait_procs(alive, timeout=timeout)
    return len(ordered)


def _depth(proc) -> int:
    import psutil

    depth = 0
    try:
        while proc.ppid():
            proc = psutil.Process(proc.ppid())
            depth += 1
    except (psutil.Error, OSError):
        pass
    return depth


def this_agent_pid() -> int | None:
    """The pid of the session's agent process: the ancestor whose own parent no longer carries
    the session id, so a worker's own shells and children resolve to it. Falls back to this
    process's pid."""

    return _os_pid()


def _os_pid() -> int:
    import os

    return os.getpid()
