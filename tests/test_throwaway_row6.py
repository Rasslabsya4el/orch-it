"""Throwaway (#421 acceptance row 6): a test that sleeps past the per-change bound (120 s)."""

import time


def test_a_deliberately_slow_test_past_the_per_change_bound():
    time.sleep(150)
