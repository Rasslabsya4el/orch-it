"""The settings of test runs (#394 decisions 1-5): defaults, a repo's own values over the machine's,
and a coded refusal of any value of the wrong shape."""

import pytest

from orch_it.settings import SettingsError
from orch_it.testruns.config import DEFAULT_COMMAND, Machine, TestSettings

REPO = "Rasslabsya4el/orch-it"


def test_no_settings_give_the_defaults():
    settings = TestSettings.from_settings({}, REPO)
    assert settings.command == DEFAULT_COMMAND and "--junitxml={report}" in settings.command
    assert (settings.bound_minutes, settings.test_env, settings.github_tests) == (15.0, {}, False)
    assert (settings.machines, settings.own_capacity, settings.github_capacity) == ((), 1, 20)


def test_a_repo_s_own_value_wins_over_the_machine_s():
    raw = {"test_run_bound_minutes": 10, "repos": {REPO: {"test_run_bound_minutes": 3},
                                                   "other/repo": {"test_run_bound_minutes": 7}}}
    assert TestSettings.from_settings(raw, REPO).bound_minutes == 3
    assert TestSettings.from_settings(raw, "else/where").bound_minutes == 10


def test_machines_are_read_in_settings_order():
    raw = {"test_machines": [
        {"id": "box", "host": "me@box", "os": "linux", "checkout": "/srv/orch-it", "capacity": 2},
        {"id": "win", "host": "win", "os": "windows", "checkout": "C:/ci/orch-it", "on": False}]}
    assert TestSettings.from_settings(raw, REPO).machines == (
        Machine("box", "me@box", "linux", "/srv/orch-it", 2),
        Machine("win", "win", "windows", "C:/ci/orch-it", 1, None, False))


@pytest.mark.parametrize("raw, field", [
    ({"test_command": "pytest"}, "test_command"),
    ({"test_command": []}, "test_command"),
    ({"test_run_bound_minutes": 0}, "test_run_bound_minutes"),
    ({"test_run_bound_minutes": True}, "test_run_bound_minutes"),
    ({"test_run_bound_minutes": float("nan")}, "test_run_bound_minutes"),
    ({"test_env": {"A": 1}}, "test_env"),
    ({"test_env": {"BAD NAME": "x"}}, "test_env"),
    ({"github_tests": "yes"}, "github_tests"),
    ({"own_capacity": -1}, "own_capacity"),
    ({"repos": []}, "repos"),
    ({"test_machines": {}}, "test_machines"),
    ({"test_machines": [{"id": "a", "host": "h", "os": "beos", "checkout": "/x"}]}, "test_machines[0].os"),
    ({"test_machines": [{"id": "a b", "host": "h", "os": "linux", "checkout": "/x"}]}, "test_machines[0].id"),
    ({"test_machines": [{"id": "a", "host": "h", "os": "linux", "checkout": "/x"},
                        {"id": "a", "host": "h", "os": "linux", "checkout": "/y"}]}, "test_machines[1].id"),
    ({"test_machines": [{"id": "a", "host": "", "os": "linux", "checkout": "/x"}]}, "test_machines[0].host"),
])
def test_a_value_of_the_wrong_shape_is_refused_naming_it(raw, field):
    with pytest.raises(SettingsError) as refused:
        TestSettings.from_settings(raw, REPO)
    assert refused.value.code == "bad_setting" and str(refused.value) == f"bad_setting: {field}"


@pytest.mark.parametrize("raw, field", [
    ({"test_run_bound_minutes": int("1" + "0" * 400)}, "test_run_bound_minutes"),
    ({"test_run_bound_minutes": 10_081}, "test_run_bound_minutes"),
    ({"test_env": {"A": "x\u0000y"}}, "test_env"),
    ({"test_command": ["python", "-c", "x\u0000"]}, "test_command"),
])
def test_an_absurd_bound_or_a_nul_is_refused_as_a_bad_setting(raw, field):
    with pytest.raises(SettingsError) as refused:
        TestSettings.from_settings(raw, REPO)
    assert refused.value.code == "bad_setting" and str(refused.value) == f"bad_setting: {field}"


def test_a_repo_s_override_matches_its_name_whatever_the_case():
    raw = {"repos": {"rasslabsya4el/ORCH-IT": {"test_run_bound_minutes": 3}}}
    assert TestSettings.from_settings(raw, REPO).bound_minutes == 3
