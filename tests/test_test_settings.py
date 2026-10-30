"""The settings of test runs (#394 decisions 1-5): defaults, a repo's own values over the machine's,
and a coded refusal of any value of the wrong shape."""

import pytest

from orch_it.settings import SettingsError
from orch_it.testruns.config import DEFAULT_COMMAND, Machine, TestSettings

REPO = "acme-x/widgets-y"


def test_no_settings_give_the_defaults():
    settings = TestSettings.from_settings({}, REPO)
    assert settings.command == DEFAULT_COMMAND and "--junitxml={report}" in settings.command
    assert (settings.bound_minutes, settings.test_env, settings.github_tests) == (15.0, {}, True)
    assert (settings.machines, settings.own_capacity, settings.github_capacity) == ((), 1, 20)


def test_github_tests_set_false_is_the_opt_out():
    assert TestSettings.from_settings({"github_tests": False}, REPO).github_tests is False


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


def test_a_machine_may_name_its_origin_and_extra_ssh_options():
    raw = {"test_machines": [{"id": "box", "host": "me@box", "os": "linux", "checkout": "/srv/x",
                              "origin": "https://example.org/me/x.git", "ssh_options": ["-p", "2222"]}]}
    [box] = TestSettings.from_settings(raw, REPO).machines
    assert (box.origin, box.ssh_options) == ("https://example.org/me/x.git", ("-p", "2222"))
    [plain] = TestSettings.from_settings(
        {"test_machines": [{"id": "b", "host": "h", "os": "linux", "checkout": "/x"}]}, REPO).machines
    assert (plain.origin, plain.ssh_options) == (None, ())


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
    ({"test_machines": [{"id": "a", "host": "-oProxyCommand=x", "os": "linux", "checkout": "/x"}]},
     "test_machines[0].host"),
    ({"test_machines": [{"id": "a", "host": "h", "os": "linux", "checkout": "/x", "origin": ""}]},
     "test_machines[0].origin"),
    ({"test_machines": [{"id": "a", "host": "h", "os": "linux", "checkout": "/x", "ssh_options": "-p 22"}]},
     "test_machines[0].ssh_options"),
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
    raw = {"repos": {"acme-x/WIDGETS-Y": {"test_run_bound_minutes": 3}}}
    assert TestSettings.from_settings(raw, REPO).bound_minutes == 3
