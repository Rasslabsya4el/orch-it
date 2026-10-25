"""A bounded run of one command (#394 decision 2, the `cut` row): the bound is wall time, and a
machine that slept across the bound makes the cut `suspended`. The wall clock is handed in, so a
sleep is a jump of that clock."""

import sys

from orch_it.testruns import process


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
                               env=None, bound_seconds=bound_seconds, log_path=tmp_path / "log",
                               wall=wall, poll_seconds=0.05)


def test_a_sleep_that_carries_the_run_past_its_bound_is_slept(tmp_path):
    done = sleeper(tmp_path, JumpingClock(jump=7200, at_call=3), bound_seconds=600)
    assert (done.cut, done.slept) == (True, True)


def test_a_short_sleep_long_before_the_bound_is_not_slept(tmp_path):
    done = sleeper(tmp_path, JumpingClock(jump=120, at_call=3), bound_seconds=130)
    assert (done.cut, done.slept) == (True, False)


def test_a_command_that_ends_inside_its_bound_is_not_cut(tmp_path):
    done = process.run_bounded([sys.executable, "-c", "raise SystemExit(4)"], cwd=tmp_path, env=None,
                               bound_seconds=60, log_path=tmp_path / "log")
    assert (done.rc, done.cut, done.slept) == (4, False, False)
