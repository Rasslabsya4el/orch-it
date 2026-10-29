"""One helper starts and stops a process tree on every OS (#396 decision 1): the whole tree dies
on `kill`, a second `kill` is harmless, output goes where the caller says."""

import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import machine_repos
from machine_repos import alive, dead_within
from orch_it.proctree import ProcessTree

PARENT = ("import subprocess, sys, time\n"
          "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
          "open(sys.argv[1], 'w').write(str(kid.pid))\n"
          "time.sleep(600)\n")


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def start_with_a_child(tmp_path):
    """The tree and the pid its process wrote down for its own child (read within a bound)."""
    pidfile = tmp_path / "child.pid"
    tree = ProcessTree.start([sys.executable, "-c", PARENT, str(pidfile)], cwd=tmp_path, env=None,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while not (pidfile.exists() and pidfile.read_text()):
        assert time.monotonic() < deadline, "the child was never started"
        time.sleep(0.05)
    return tree, int(pidfile.read_text())


def test_kill_takes_the_whole_tree_and_a_second_kill_is_harmless(tmp_path):
    tree, child = start_with_a_child(tmp_path)
    assert alive(tree.pid) and alive(child)
    tree.kill()
    tree.kill()
    assert tree.poll() is not None and tree.returncode is not None
    assert dead_within(child)


GONE = 2**22 + 4099  # above Linux's largest pid_max: no process has this pid, so /proc has no entry


def test_a_process_that_vanishes_while_it_is_probed_reads_as_dead(monkeypatch):
    """The race of guards PR #39's linux job: the process answers the signal-0 probe, is gone
    before its /proc entry is read, and is gone when a kill follows. A gone process is dead,
    never an error."""

    probes = []

    def kill(pid, sig):
        probes.append(sig)
        if sig == 0 and len(probes) == 1:
            return  # still there for the first probe
        raise ProcessLookupError(3, "No such process")

    class LinuxProc:
        """/proc as Linux has it, with no entry left for the vanished pid."""

        def __init__(self, path):
            self.path = str(path)

        def exists(self):
            return self.path == "/proc/self"

        def read_text(self):
            raise FileNotFoundError(2, "No such file or directory", self.path)

    monkeypatch.setattr(machine_repos, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(machine_repos, "Path", LinuxProc)
    monkeypatch.setattr(machine_repos.os, "kill", kill)
    assert machine_repos.alive(GONE) is False
    assert machine_repos.dead_within(GONE, seconds=0.3) is True


def test_a_kill_that_finds_the_process_gone_counts_it_dead(monkeypatch):
    """The probe saw it alive, then it ended on its own before the kill."""

    seen = iter([True, True, True])
    monkeypatch.setattr(machine_repos, "alive", lambda pid: next(seen, True))

    def kill(pid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(machine_repos.os, "kill", kill)
    assert machine_repos.dead_within(GONE, seconds=0.2) is True


def test_output_goes_to_the_file_the_caller_gave(tmp_path):
    out = tmp_path / "out.log"
    with open(out, "ab") as f:
        tree = ProcessTree.start([sys.executable, "-c", "print('to the file'); import sys; "
                                  "print('err', file=sys.stderr)"],
                                 cwd=tmp_path, env=None, stdout=f, stderr=subprocess.STDOUT)
        tree.process.wait(timeout=30)
        tree.kill()
    text = out.read_text()
    assert "to the file" in text and "err" in text


def test_a_command_that_cannot_start_raises_os_error(tmp_path):
    with pytest.raises(OSError):
        ProcessTree.start(["no-such-program-orch-it"], cwd=tmp_path, env=None,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
