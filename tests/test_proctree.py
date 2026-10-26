"""One helper starts and stops a process tree on every OS (#396 decision 1): the whole tree dies
on `kill`, a second `kill` is harmless, output goes where the caller says."""

import subprocess
import sys

from orch_it.proctree import ProcessTree
from machine_repos import alive, dead_within

PARENT = ("import subprocess, sys, time\n"
          "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
          "print(kid.pid, flush=True)\n"
          "time.sleep(600)\n")


def start(tmp_path, code=PARENT, **kw):
    return ProcessTree.start([sys.executable, "-c", code], cwd=tmp_path, env=None,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kw)


def test_kill_takes_the_whole_tree_and_a_second_kill_is_harmless(tmp_path):
    tree = start(tmp_path)
    child = int(tree.process.stdout.readline())
    assert alive(tree.process.pid) and alive(child)
    tree.kill()
    tree.kill()
    assert tree.process.poll() is not None
    assert dead_within(child)
    tree.process.stdout.close()


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
    try:
        ProcessTree.start(["no-such-program-orch-it"], cwd=tmp_path, env=None,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return
    raise AssertionError("expected OSError")
