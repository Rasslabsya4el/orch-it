"""Review hardening of process judging (#395 unit 4): a target the guard cannot read is refused
as `unresolved`, never silently passed; a crafted pattern cannot hang the guard; the refusal
reason is a closed set."""

import time

import pytest

from orch_it import guards  # noqa: F401
from orch_it.guards import processes
from orch_it.guards.processes import Proc
from orch_it.guards.triggers import Matcher, Stop

SESSION = "sess-123"
AGENT = 1000


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


class Tree:
    def __init__(self, rows):
        self._p = {r["pid"]: r for r in rows}

    def get(self, pid):
        r = self._p.get(pid)
        if r is None:
            return None
        return Proc(pid=pid, create_time=r.get("start", 100.0), ppid=r.get("ppid"),
                    env_session=r.get("env"))

    def live_pids(self):
        return list(self._p)

    def ppid(self, pid):
        r = self._p.get(pid)
        return r.get("ppid") if r else None

    def name(self, pid):
        r = self._p.get(pid)
        return r.get("name") if r else None

    def cmdline(self, pid):
        r = self._p.get(pid)
        return r.get("cmd", r.get("name", "")) if r else None


def own_row(pid, **kw):
    kw.update(pid=pid, env=SESSION)
    kw.setdefault("ppid", 1)
    return kw


def judge(stop, tree):
    return processes.judge_stop(stop, facts=tree, session_id=SESSION, agent_pid=AGENT,
                                recorded=set())


def pid_stop(*pids):
    return Stop(tuple(pids), (), False, False, "kill", "seg")


class UnreadableTree(Tree):
    def __init__(self, rows, unreadable):
        super().__init__(rows)
        self._unreadable = set(unreadable)

    def get(self, pid):
        if pid in self._unreadable:
            return processes.UNREADABLE
        return super().get(pid)


def test_a_stop_of_an_unreadable_pid_is_refused_as_unresolved():
    tree = UnreadableTree([{"pid": 50}], unreadable={50})
    refusal = judge(pid_stop(50), tree)
    assert refusal is not None and refusal.reason == "unresolved"


def test_a_stop_whose_live_list_cannot_be_read_is_unresolved():
    class Blind(Tree):
        def live_pids(self):
            raise OSError("cannot list")

    stop = Stop((), (Matcher("name_glob", "python"),), False, False, "killall", "seg")
    assert judge(stop, Blind([own_row(1, name="python")])).reason == "unresolved"


def test_a_stop_with_an_invalid_pattern_is_unresolved_not_passed():
    tree = Tree([own_row(50, name="python", cmd="python x")])
    stop = Stop((), (Matcher("cmdline_regex", "*bad("),), False, False, "pkill", "seg")
    assert judge(stop, tree).reason == "unresolved"


def test_a_catastrophic_pattern_does_not_hang_and_is_unresolved():
    # a non-matching tail forces the exponential backtrack this pattern is known for
    tree = Tree([own_row(50, name="x", cmd="a" * 36 + "!")])
    stop = Stop((), (Matcher("cmdline_regex", "(a+)+$"),), False, False, "pkill", "seg")
    start = time.monotonic()
    refusal = judge(stop, tree)
    assert time.monotonic() - start < 10
    assert refusal is not None and refusal.reason == "unresolved"


def test_an_unknown_matcher_kind_is_unresolved():
    tree = Tree([own_row(50, name="python")])
    stop = Stop((), (Matcher("made_up", "python"),), False, False, "pkill", "seg")
    assert judge(stop, tree).reason == "unresolved"


def test_an_empty_session_id_matches_no_process_by_env():
    facts = Tree([{"pid": 50, "ppid": 1, "env": ""}])
    assert processes.is_own(50, facts=facts, session_id="", agent_pid=AGENT, recorded=set()) is False


def test_a_stop_refusal_reason_is_a_closed_set():
    with pytest.raises(ValueError):
        processes.StopRefusal("made_up", None, "kill")
    assert processes.STOP_REFUSALS == ("foreign_process", "unresolved")


def test_this_agent_pid_walks_up_to_the_top_of_the_session():
    import os

    me = os.getpid()
    rows = [
        {"pid": me, "ppid": 500, "start": 30.0, "env": SESSION},
        {"pid": 500, "ppid": 400, "start": 20.0, "env": SESSION},   # the session's top process
        {"pid": 400, "ppid": 1, "start": 10.0, "env": "launcher"},  # outside the session
    ]
    assert processes.this_agent_pid(Tree(rows), SESSION) == 500


def test_a_name_target_matches_with_or_without_the_exe_suffix():
    from orch_it.guards.processes import _name_matches
    assert _name_matches("explorer.exe", "explorer", exact=False)
    assert _name_matches("explorer.exe", "explorer.exe", exact=False)
    assert _name_matches("python.exe", "py*", exact=False)
    assert _name_matches("node", "node", exact=True)
    assert _name_matches("explorer.exe", "explorer", exact=True)
    assert not _name_matches("notepad.exe", "explorer", exact=False)
