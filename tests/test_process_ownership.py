"""Who owns a process (#395 decision 3): a process is own when its environment carries this
session's id, or its parent chain (each parent older than its child) reaches the session's
agent process, or the store recorded it as a `process` this session started with that pid and
start time. Nothing proven is foreign. The rule is pure over a `Facts` reading of the process
tree, faked here; the psutil reading is tested against real processes elsewhere."""

import pytest
from orch_it.guards import processes
from orch_it.guards.processes import Proc


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


class FakeFacts:
    """A process tree held in memory for the ownership rule to read."""

    def __init__(self, procs):
        self._procs = {p.pid: p for p in procs}

    def get(self, pid):
        return self._procs.get(pid)


SESSION = "sess-123"
AGENT = 1000


def own(pid, facts, *, session=SESSION, agent=AGENT, recorded=()):
    return processes.is_own(pid, facts=facts, session_id=session, agent_pid=agent,
                            recorded=set(recorded))


def proc(pid, *, ppid=None, start=100.0, env_session=None):
    return Proc(pid=pid, create_time=start, ppid=ppid, env_session=env_session)


def test_a_process_carrying_this_sessions_id_is_own():
    facts = FakeFacts([proc(50, env_session=SESSION)])
    assert own(50, facts) is True


def test_a_process_carrying_another_sessions_id_is_foreign():
    facts = FakeFacts([proc(50, env_session="other-session", ppid=1)])
    assert own(50, facts) is False


def test_a_process_whose_parent_chain_reaches_the_agent_is_own():
    facts = FakeFacts([
        proc(AGENT, start=10.0),
        proc(60, ppid=AGENT, start=20.0),
        proc(70, ppid=60, start=30.0),
    ])
    assert own(70, facts) is True


def test_a_chain_broken_by_a_reused_pid_is_not_followed():
    # pid 60's recorded parent is the agent, but 60 is OLDER than the agent: a reused pid, so the
    # link does not count and nothing else proves 70 own
    facts = FakeFacts([
        proc(AGENT, start=50.0),
        proc(60, ppid=AGENT, start=20.0),  # older than its supposed parent
        proc(70, ppid=60, start=30.0),
    ])
    assert own(70, facts) is False


def test_a_process_the_store_recorded_with_its_pid_and_start_is_own():
    facts = FakeFacts([proc(80, ppid=1, start=42.0)])
    assert own(80, facts, recorded=[(80, 42.0)]) is True


def test_a_recorded_pid_with_a_different_start_is_foreign():
    # the store recorded pid 80 started at 42.0; this live pid 80 started at 99.0, so it is a
    # different, reused process, foreign
    facts = FakeFacts([proc(80, ppid=1, start=99.0)])
    assert own(80, facts, recorded=[(80, 42.0)]) is False


def test_a_process_not_in_the_tree_is_foreign():
    assert own(999, FakeFacts([])) is False


def test_a_chain_that_never_reaches_the_agent_is_foreign():
    facts = FakeFacts([proc(60, ppid=1, start=20.0), proc(70, ppid=60, start=30.0)])
    assert own(70, facts) is False


def test_a_chain_with_a_cycle_ends_and_is_foreign():
    facts = FakeFacts([proc(60, ppid=70, start=20.0), proc(70, ppid=60, start=30.0)])
    assert own(70, facts) is False


# --- judging a stop command against the live tree --------------------------------------------

from orch_it.guards.triggers import Matcher, Stop


class Tree:
    """A live process tree for judging a stop: each process has a pid, parent, start, the
    session in its environment, a name and a command line."""

    def __init__(self, rows):
        self._p = {}
        for row in rows:
            self._p[row["pid"]] = row

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
    kw["env"] = SESSION
    kw.setdefault("ppid", 1)
    kw["pid"] = pid
    return kw


def foreign_row(pid, **kw):
    kw["env"] = "other"
    kw.setdefault("ppid", 1)
    kw["pid"] = pid
    return kw


def judge(stop, tree, recorded=()):
    return processes.judge_stop(stop, facts=tree, session_id=SESSION, agent_pid=AGENT,
                                recorded=set(recorded))


def stop_pids(*pids, tree=False, opaque=False):
    return Stop(tuple(pids), (), opaque, tree, "kill", "seg")


def test_a_stop_of_an_own_pid_is_allowed():
    tree = Tree([own_row(50, name="python")])
    assert judge(stop_pids(50), tree) is None


def test_a_stop_of_a_foreign_pid_is_refused():
    tree = Tree([foreign_row(50, name="python")])
    refusal = judge(stop_pids(50), tree)
    assert refusal is not None and refusal.reason == "foreign_process" and refusal.pid == 50


def test_an_opaque_stop_is_refused_as_unresolved():
    tree = Tree([own_row(50)])
    assert judge(stop_pids(50, opaque=True), tree).reason == "unresolved"


def test_a_name_stop_matching_a_foreign_process_refuses_the_whole_command():
    tree = Tree([own_row(50, name="python.exe"), foreign_row(60, name="python.exe")])
    stop = Stop((), (Matcher("name_glob", "python.exe"),), False, False, "taskkill", "seg")
    assert judge(stop, tree).reason == "foreign_process"


def test_a_pattern_stop_matching_only_own_processes_is_allowed():
    tree = Tree([own_row(50, name="node", cmd="node runserver"),
                 foreign_row(60, name="node", cmd="node other")])
    stop = Stop((), (Matcher("cmdline_regex", "runserver"),), False, False, "pkill", "seg")
    assert judge(stop, tree) is None


def test_a_tree_stop_judges_every_descendant():
    tree = Tree([own_row(50, name="sh"), foreign_row(70, ppid=50, name="grep")])
    # pid 50 is own but its child 70 is foreign; a tree stop must refuse
    assert judge(stop_pids(50, tree=True), tree).reason == "foreign_process"


def test_a_stop_that_matches_no_live_process_is_allowed():
    tree = Tree([own_row(50, name="python")])
    stop = Stop((), (Matcher("name_glob", "ruby"),), False, False, "killall", "seg")
    assert judge(stop, tree) is None
    assert judge(stop_pids(999), tree) is None  # a pid that is not live: nothing to stop


# --- #419 unit 3: a parent that died, a worker that ended, a process with neither fact ---------

def test_a_process_whose_git_bash_parent_died_stays_own_by_its_session_id():
    # Git Bash (pid 40) was killed; its child is re-parented to pid 1, so the parent chain no
    # longer reaches the agent. The session id still in its environment proves it own.
    facts = FakeFacts([proc(50, ppid=1, start=30.0, env_session=SESSION)])
    assert own(50, facts) is True


def test_a_process_whose_parent_is_no_longer_in_the_tree_stays_own_by_its_session_id():
    facts = FakeFacts([proc(50, ppid=40, start=30.0, env_session=SESSION)])  # pid 40 not live
    assert own(50, facts) is True


def test_a_session_stop_ends_a_git_bash_child_whose_bash_parent_died():
    tree = Tree([own_row(50, name="python.exe", cmd="python script.py", ppid=40)])
    assert judge(stop_pids(50, tree=True), tree) is None


def test_a_process_started_by_a_worker_is_the_orch_sessions_own_after_the_worker_ended():
    # the worker subagent (pid 45, not live any more) started pid 50 with the ORCH session's id
    # in its environment; the ORCH agent (AGENT) is alive but not in pid 50's chain
    facts = FakeFacts([proc(AGENT, start=10.0), proc(50, ppid=45, start=30.0, env_session=SESSION)])
    assert own(50, facts) is True


def test_orch_stops_a_pytest_its_finished_worker_left_running():
    tree = Tree([own_row(50, name="python.exe", cmd="python -m pytest tests", ppid=45)])
    stop = Stop((), (Matcher("cmdline_regex", "pytest"),), False, False, "taskkill", "seg")
    assert judge(stop, tree) is None


def test_a_process_with_no_session_id_whose_parent_died_is_foreign():
    facts = FakeFacts([proc(50, ppid=40, start=30.0, env_session=None)])
    assert own(50, facts) is False


def test_another_sessions_process_whose_parent_died_is_refused_as_foreign_on_stop():
    tree = Tree([foreign_row(50, name="python.exe", cmd="python -m pytest", ppid=40)])
    refusal = judge(stop_pids(50), tree)
    assert refusal is not None and refusal.reason == "foreign_process" and refusal.pid == 50


def test_another_sessions_pytest_left_by_its_worker_is_refused_as_foreign_on_stop():
    tree = Tree([foreign_row(50, name="python.exe", cmd="python -m pytest tests", ppid=45)])
    stop = Stop((), (Matcher("cmdline_regex", "pytest"),), False, False, "taskkill", "seg")
    refusal = judge(stop, tree)
    assert refusal is not None and refusal.reason == "foreign_process" and refusal.pid == 50
