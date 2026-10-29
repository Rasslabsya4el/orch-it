"""A bounded run of one command (#394 decision 2, the `cut` row): the bound is wall time, and a
machine that slept across the bound makes the cut `suspended`. The wall clock is handed in, so a
sleep is a jump of that clock."""

import sys
import time

import pytest

from orch_it.testruns import process


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


class JumpingClock:
    """Wall time that runs at 1 s per call and jumps `jump` seconds once, at call `at_call`."""

    def __init__(self, jump: float, at_call: int):
        self.now, self.calls, self.jump, self.at_call = 1000.0, 0, jump, at_call

    def __call__(self) -> float:
        self.calls += 1
        self.now += self.jump if self.calls == self.at_call else 1.0
        return self.now


def sleeper(tmp_path, wall, bound_seconds):
    return process.run_bounded([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path,
                               env=None, bound_seconds=bound_seconds,
                               wall=wall, poll_seconds=0.05)


def test_a_sleep_that_carries_the_run_past_its_bound_is_slept(tmp_path):
    done = sleeper(tmp_path, JumpingClock(jump=7200, at_call=3), bound_seconds=600)
    assert (done.cut, done.slept) == (True, True)


def test_a_short_sleep_long_before_the_bound_is_not_slept(tmp_path):
    done = sleeper(tmp_path, JumpingClock(jump=120, at_call=3), bound_seconds=130)
    assert (done.cut, done.slept) == (True, False)


class BackwardClock:
    """Wall time that steps back 100 s once, at call 3, and then runs on."""

    def __init__(self):
        self.now, self.calls = 1000.0, 0

    def __call__(self) -> float:
        self.calls += 1
        self.now += -100.0 if self.calls == 3 else 0.05
        return self.now


def test_a_wall_clock_stepping_back_does_not_stretch_the_bound(tmp_path):
    started = time.monotonic()
    done = sleeper(tmp_path, BackwardClock(), bound_seconds=2)
    assert done.cut and time.monotonic() - started < 15


def test_a_command_flooding_its_output_keeps_only_a_bounded_tail(tmp_path):
    flood = "import sys\nwhile True: sys.stdout.write('x' * 65536)"
    done = process.run_bounded([sys.executable, "-c", flood], cwd=tmp_path, env=None,
                               bound_seconds=3)
    assert done.cut
    assert len(done.log_tail) <= process.LOG_TAIL_BYTES
    assert not (tmp_path / "log").exists()


def test_a_command_that_cannot_start_is_command_missing(tmp_path):
    for argv in (["no-such-program-orch-it"], [sys.executable, "-c", "x\u0000"]):
        with pytest.raises(process.CommandMissing):
            process.run_bounded(argv, cwd=tmp_path, env=None, bound_seconds=5)


def test_a_command_that_ends_inside_its_bound_is_not_cut(tmp_path):
    done = process.run_bounded([sys.executable, "-c", "raise SystemExit(4)"], cwd=tmp_path, env=None,
                               bound_seconds=60)
    assert (done.rc, done.cut, done.slept) == (4, False, False)


def test_a_run_told_to_abort_is_killed_at_once_and_not_waited_for(tmp_path):
    import threading
    abort = threading.Event()
    threading.Timer(0.5, abort.set).start()
    started = time.monotonic()
    done = process.run_bounded([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path,
                               env=None, bound_seconds=600, poll_seconds=0.05, abort=abort)
    assert done.rc is None and time.monotonic() - started < 15
