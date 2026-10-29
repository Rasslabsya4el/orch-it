"""Process ownership and stop against real processes (#395 decision 3, row 5): the psutil reading
tells own from foreign on this machine, a stop of a foreign process is refused and leaves it
alive, a stop of an own tree leaves nothing. Every process these tests start is cleaned up."""

import os
import subprocess
import sys
import time

import psutil
import pytest

from orch_it.guards import processes
from orch_it.guards.processes import PsutilFacts
from orch_it.guards.triggers import Matcher, Stop

SESSION = "test-session-" + str(os.getpid())
VAR = processes.DEFAULT_SESSION_ENV_VAR
SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def spawn(session_value, argv=SLEEP):
    env = dict(os.environ)
    if session_value is None:
        env.pop(VAR, None)
    else:
        env[VAR] = session_value
    return subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=NO_WINDOW)


class Detached:
    """A sleeper started so it is NOT a descendant of this test process: a launcher spawns it
    detached (its own session/process group) and exits, so its parent chain never reaches this
    agent. That is what a process of another session looks like (P947)."""

    def __init__(self, pid):
        self.pid = pid


def spawn_detached(session_value):
    import tempfile

    env = dict(os.environ)
    env[VAR] = session_value
    pid_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pid")
    pid_file.close()
    detach = ("start_new_session=True" if sys.platform != "win32"
              else "creationflags=0x00000008|0x00000200")  # DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP
    launcher = (
        "import subprocess,sys,os\n"
        f"p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'], {detach},\n"
        "  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "open(sys.argv[1],'w').write(str(p.pid))\n")
    subprocess.run([sys.executable, "-c", launcher, pid_file.name], env=env,
                   creationflags=NO_WINDOW, timeout=15, check=True)
    deadline = time.time() + 10
    pid = None
    while time.time() < deadline:
        text = open(pid_file.name).read().strip()
        if text:
            pid = int(text)
            break
        time.sleep(0.05)
    os.unlink(pid_file.name)
    assert pid is not None
    # wait until the launcher has exited so the chain is truly detached
    time.sleep(0.3)
    return Detached(pid)


@pytest.fixture
def reaper():
    started = []
    yield started
    for proc in started:
        try:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.Error:
            pass
        try:
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            pass


@pytest.fixture
def reap_detached():
    pids = []
    yield pids
    for pid in pids:
        try:
            p = psutil.Process(pid)
            for child in p.children(recursive=True):
                child.kill()
            p.kill()
        except psutil.Error:
            pass


def facts():
    return PsutilFacts(VAR)


def own(pid):
    return processes.is_own(pid, facts=facts(), session_id=SESSION,
                            agent_pid=os.getpid(), recorded=set())


def test_a_child_carrying_the_session_id_is_own(reaper):
    child = spawn(SESSION)
    reaper.append(child)
    assert own(child.pid) is True


def test_a_process_of_another_session_is_foreign_even_from_the_same_user_and_folder(reap_detached):
    other = spawn_detached("a-different-session")
    reap_detached.append(other.pid)
    assert own(other.pid) is False


def test_a_child_with_no_session_env_is_own_by_its_parent_chain(reaper):
    # started by this test process (the agent) with the env var stripped: the parent chain proves it
    child = spawn(None)
    reaper.append(child)
    assert processes.is_own(child.pid, facts=facts(), session_id=SESSION,
                            agent_pid=os.getpid(), recorded=set()) is True


def test_a_recorded_pid_with_a_stale_start_time_is_foreign(reap_detached):
    other = spawn_detached("a-different-session")
    reap_detached.append(other.pid)
    real_start = psutil.Process(other.pid).create_time()
    # the store recorded this pid but with a start time 1000 s earlier: a reused pid, foreign
    recorded = {(other.pid, real_start - 1000)}
    assert processes.is_own(other.pid, facts=facts(), session_id=SESSION,
                            agent_pid=os.getpid(), recorded=recorded) is False


def judge(stop):
    return processes.judge_stop(stop, facts=facts(), session_id=SESSION,
                                agent_pid=os.getpid(), recorded=set())


STOP_FORMS = [
    lambda pid: Stop((pid,), (), False, False, "kill", "seg"),
    lambda pid: Stop((pid,), (), False, True, "taskkill", "seg"),
    lambda pid: Stop((), (Matcher("name_glob", _PROC_NAME),), False, False, "killall", "seg"),
    lambda pid: Stop((), (Matcher("cmdline_regex", "time.sleep"),), False, False, "pkill", "seg"),
]
_PROC_NAME = os.path.basename(sys.executable)


@pytest.mark.parametrize("form", STOP_FORMS)
def test_a_stop_of_a_foreign_process_is_refused_and_leaves_it_alive(reap_detached, form):
    other = spawn_detached("a-different-session")
    reap_detached.append(other.pid)
    refusal = judge(form(other.pid))
    assert refusal is not None and refusal.reason == "foreign_process"
    assert psutil.pid_exists(other.pid) and psutil.Process(other.pid).is_running()


def test_a_stop_of_an_own_child_is_allowed(reaper):
    child = spawn(SESSION)
    reaper.append(child)
    assert judge(Stop((child.pid,), (), False, False, "kill", "seg")) is None


def test_stopping_an_own_wrapper_tree_leaves_no_process(reaper):
    # a wrapper that starts a child holding a pipe; stopping the tree must end both (P521, P905)
    code = ("import subprocess,sys,time;"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
            "time.sleep(30)")
    wrapper = spawn(SESSION, [sys.executable, "-c", code])
    reaper.append(wrapper)
    deadline = time.time() + 10
    while not psutil.Process(wrapper.pid).children() and time.time() < deadline:
        time.sleep(0.1)
    tree = [wrapper.pid] + [c.pid for c in psutil.Process(wrapper.pid).children(recursive=True)]
    assert len(tree) >= 2
    processes.stop_tree(wrapper.pid)
    time.sleep(0.5)
    assert not any(psutil.pid_exists(pid) and psutil.Process(pid).is_running()
                   and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE for pid in tree)
