"""The settings of the merge flow (#400 decision 5): every bound has a default, a repo's own value
wins over the machine's, and a value of the wrong shape is a coded refusal naming its key."""

import pytest

from orch_it.merge.settings import MergeSettings
from orch_it.settings import SettingsError

REPO = "owner-x/repo-y"


def test_no_settings_give_the_defaults():
    found = MergeSettings.from_settings({}, REPO)
    assert (found.pr_max_hours, found.line_minutes, found.check_minutes, found.poll_seconds) == (24, 60, 30, 30)
    assert (found.max_updates, found.lock_wait_minutes, found.lock_stale_minutes) == (3, 20, 120)
    assert (found.check_stuck_minutes, found.red_in_a_row, found.merge_method) == (60, 3, "merge")


def test_a_repo_s_own_value_wins_over_the_machine_s():
    raw = {"merge": {"pr_max_hours": 12, "merge_method": "squash"},
           "repos": {"OWNER-X/Repo-Y": {"merge": {"pr_max_hours": 6}}}}
    own = MergeSettings.from_settings(raw, REPO)
    assert (own.pr_max_hours, own.merge_method) == (6, "squash")
    assert MergeSettings.from_settings(raw, "else/where").pr_max_hours == 12


@pytest.mark.parametrize("key, value", [
    ("pr_max_hours", 0), ("pr_max_hours", -1), ("pr_max_hours", True), ("pr_max_hours", "24"),
    ("pr_max_hours", float("nan")), ("pr_max_hours", 10 ** 400), ("pr_max_hours", 7 * 24 + 1),
    ("line_minutes", 7 * 24 * 60 + 1), ("poll_seconds", 0), ("poll_seconds", 1e-9), ("max_updates", 0),
    ("max_updates", 1001), ("max_updates", 2.5), ("red_in_a_row", 0),
    ("merge_method", "fast-forward"), ("merge_method", None), ("lock_wait_minutes", -0.5),
    ("unknown_key", 1),
])
def test_a_value_of_the_wrong_shape_is_refused_by_its_key(key, value):
    with pytest.raises(SettingsError) as caught:
        MergeSettings.from_settings({"merge": {key: value}}, REPO)
    assert caught.value.code == "bad_setting" and key in str(caught.value)


@pytest.mark.parametrize("raw", [{"merge": []}, {"merge": "x"}, {"repos": []}, {"repos": {REPO: {"merge": 3}}}])
def test_a_section_that_is_no_object_is_refused(raw):
    with pytest.raises(SettingsError) as caught:
        MergeSettings.from_settings(raw, REPO)
    assert caught.value.code == "bad_setting"


def test_a_repo_written_two_ways_is_no_answer():
    with pytest.raises(SettingsError) as caught:
        MergeSettings.from_settings({"repos": {REPO: {}, REPO.upper(): {}}}, REPO)
    assert "written 2 ways" in str(caught.value)


def test_the_bounds_at_their_edges_are_taken():
    found = MergeSettings.from_settings({"merge": {"pr_max_hours": 168, "poll_seconds": 1, "max_updates": 1000}}, REPO)
    assert (found.pr_max_hours, found.poll_seconds, found.max_updates) == (168, 1, 1000)


def test_a_stale_lock_bound_must_outlast_a_line_run():
    # a lock younger than the longest line run would be reported stale while its line still runs
    with pytest.raises(SettingsError) as caught:
        MergeSettings.from_settings({"merge": {"line_minutes": 90, "lock_stale_minutes": 60}}, REPO)
    assert "lock_stale_minutes" in str(caught.value)
