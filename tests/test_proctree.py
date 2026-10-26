"""One helper starts and stops a process tree on every OS (#396 decision 1): the whole tree dies
on `kill`, a second `kill` is harmless, output goes where the caller says."""

import subprocess
import sys
import time

import pytest

from machine_repos import alive, dead_within
from orch_it.proctree import ProcessTree

PARENT = ("import subprocess, sys, time\n"
          "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
          "open(sys.argv[1], 'w').write(str(kid.pid))\n"
          "time.sleep(600)\n")


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
