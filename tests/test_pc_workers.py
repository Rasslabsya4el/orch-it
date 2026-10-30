"""A place that is a PC runs a suite on all the cores free on it (#414 item 11, last clause): K is the machine's
logical cores minus the cores its current load shows busy (at least 1, capped by a setting); a pytest command
that can take `-n K` gets it once, the run's record names K, and the first line of `tests run`'s output says
`workers: K`. A command that is not pytest, one that already carries its workers and a PC without xdist keep the
old run. The cores and the load are read through one function a test replaces."""

import importlib.util
import json
import sys

import pytest
from required_names import LINUX_OS

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.testruns import workers
from orch_it.testruns.config import Machine, SettingsError, TestSettings
from orch_it.testruns.environment import clean_environment
from orch_it.testruns.runner import run_tests

REPO = "acme-x/widgets-y"
PYTEST = ("{python}", "-m", "pytest", "-q", "-p", "orch_it_ids", "--junitxml={report}")
NEEDS_XDIST = pytest.mark.skipif(importlib.util.find_spec("xdist") is None, reason="xdist is not installed here")


def plan(command=PYTEST, cores=12, busy=0.0, xdist=True, cap=None):
    return workers.plan(command, cap=cap, load=lambda: (cores, busy), xdist=lambda: xdist)


# the free cores


@pytest.mark.parametrize("cores, busy, free", [
    (12, 0.48, 12),   # a few percent of load is not a busy core
    (12, 4.0, 8),
    (12, 11.6, 1),    # never below one
    (4, 9.0, 1),      # a load over the cores is still one worker
    (1, 0.0, 1),
    (6, None, 6),     # a load that cannot be read counts as idle
])
def test_free_cores_are_the_logical_cores_minus_those_the_load_shows_busy(cores, busy, free):
    assert workers.free_cores(lambda: (cores, busy)) == free


def test_cores_that_cannot_be_read_are_one_worker():
    assert workers.free_cores(lambda: (None, None)) == 1


def test_the_real_reader_answers_with_cores_and_a_load():
    cores, busy = workers.system_load()
    assert cores >= 1 and (busy is None or busy >= 0)


# the command


def test_a_pytest_command_gets_n_k_once_k_the_free_cores():
    got = plan(cores=12, busy=4.0)
    assert got.workers == 8 and got.note is None
    assert got.command.count("-n") == 1 and got.command[got.command.index("-n") + 1] == "8"
    assert [a for a in got.command if a != "-n" and a != "8"] == [a for a in PYTEST]


def test_this_interpreter_spelled_out_gets_n_k_too():
    got = plan((sys.executable, "-m", "pytest", "-q"))
    assert got.command == (sys.executable, "-m", "pytest", "-n", "12", "-q") and got.workers == 12


@pytest.mark.parametrize("command", [
    ("pytest", "-q"),                                   # a bare executable: its interpreter is not known to be ours
    (sys.executable + "-other", "-m", "pytest", "-q"),  # another interpreter than the one the probe asked
    ("python-elsewhere", "-m", "pytest", "-q"),
])
def test_a_command_of_another_interpreter_than_the_probed_one_runs_one_worker(command):
    got = plan(command)
    assert (got.command, got.workers, got.note) == (command, 1, "workers_unavailable")


def test_the_cap_setting_lowers_k_and_never_raises_it():
    assert plan(cores=12, cap=3).workers == 3
    assert plan(cores=2, cap=8).workers == 2


def test_one_free_core_runs_the_old_command():
    got = plan(cores=1)
    assert (got.command, got.workers, got.note) == (PYTEST, 1, None)


@pytest.mark.parametrize("carried, count", [
    (("-n", "2"), 2), (("-n4",), 4), (("--numprocesses=3",), 3), (("--numprocesses", "5"), 5),
    (("-n", "auto"), None), (("-nauto",), None), (("-n=6",), 6), (("-nlogical",), None),
    (("-n", "0"), 1), (("--numprocesses=0",), 1), (("-n=0",), 1),  # `-n 0`: xdist's switch-off, one process
])
def test_a_command_that_carries_its_workers_is_left_alone(carried, count):
    command = (*PYTEST, *carried)
    got = plan(command)
    assert got.command == command and got.workers == count and got.note == "workers_in_command"


@pytest.mark.parametrize("off", [("-p", "no:xdist"), ("-pno:xdist",), ("-p", "no:xdist.plugin")])
def test_a_command_that_switches_xdist_off_is_left_alone_with_one_worker(off):
    command = (*PYTEST, *off)
    got = plan(command)
    assert (got.command, got.workers, got.note) == (command, 1, "workers_in_command")


@pytest.mark.parametrize("flag", ["--no-header", "-nx", "-ntests", "--no-cov", "-pno:cacheprovider"])
def test_a_flag_that_only_looks_like_the_workers_option_is_not_one(flag):
    got = plan((*PYTEST, flag))
    assert got.workers == 12 and got.note is None and got.command.count("-n") == 1


def test_a_pc_without_xdist_runs_one_worker_and_says_so():
    got = plan(xdist=False)
    assert (got.command, got.workers, got.note) == (PYTEST, 1, "workers_unavailable")


def test_a_command_that_is_not_pytest_is_left_alone_with_no_workers():
    command = ("{python}", "-c", "print('pytest')")
    got = plan(command)
    assert (got.command, got.workers, got.note) == (command, None, None)


def test_the_xdist_probe_reads_this_interpreter(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "xdist" else object())
    assert workers.xdist_importable() is False
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert workers.xdist_importable() is True


# the run at the own place


def suite(tmp_path, count=4):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    body = "".join(f"def test_{n}():\n    assert {n} == {n}\n\n" for n in range(count))
    (root / "test_many.py").write_text(body, "utf-8")
    return root


def facts(store, kind):
    [(detail,)] = store.query("SELECT detail FROM events WHERE kind = ? AND step = 'test_run'", (kind,)).rows
    return json.loads(detail)


@pytest.fixture
def eight_cores(monkeypatch):
    monkeypatch.setattr(workers, "system_load", lambda: (8, 0.0))


@NEEDS_XDIST
def test_the_own_run_uses_k_workers_and_the_record_names_them(tmp_path, eight_cores):
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=suite(tmp_path), repo=REPO, settings=TestSettings(own_workers_max=2))
    assert (done.outcome, done.collected, done.workers) == ("passed", 4, 2)
    assert facts(store, "step.started")["workers"] == 2 and facts(store, "step.ended")["workers"] == 2


@NEEDS_XDIST
def test_all_the_free_cores_are_used_when_nothing_caps_them(tmp_path, eight_cores):
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=suite(tmp_path), repo=REPO, settings=TestSettings())
    assert (done.outcome, done.workers) == ("passed", 8)


@NEEDS_XDIST
def test_an_ids_file_still_selects_the_tests_under_workers(tmp_path, eight_cores):
    ids = tmp_path / "ids.txt"
    ids.write_text("test_many::test_1\ntest_many::test_3\n", "utf-8")
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=suite(tmp_path), repo=REPO, settings=TestSettings(own_workers_max=2),
                     ids_file=ids)
    assert (done.outcome, done.collected, done.workers) == ("passed", 2, 2)


def test_without_xdist_the_own_run_goes_on_in_one_process(tmp_path, eight_cores, monkeypatch):
    monkeypatch.setattr(workers, "xdist_importable", lambda: False)
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=suite(tmp_path), repo=REPO, settings=TestSettings())
    assert (done.outcome, done.collected, done.workers, done.workers_note) == (
        "passed", 4, 1, "workers_unavailable")
    assert facts(store, "step.ended")["workers_note"] == "workers_unavailable"


def test_a_command_that_is_not_pytest_leaves_the_record_without_workers(tmp_path, eight_cores):
    command = ("{python}", "-c", "import sys; sys.exit(0)")
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=suite(tmp_path), repo=REPO, settings=TestSettings(command=command))
    assert done.workers is None
    assert facts(store, "step.started")["workers"] is None and "workers" not in facts(store, "step.ended")


# the first line of the output


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    return path


def run_cli(tmp_path, capsys, *extra):
    code = main(["--store", str(tmp_path / "store"), "tests", "run", "--work-tree", str(suite(tmp_path)),
                 "--repo", REPO, *extra])
    return code, capsys.readouterr().out.splitlines()


@NEEDS_XDIST
def test_the_output_opens_with_the_workers_line(tmp_path, capsys, eight_cores, settings_file):
    settings_file.write_text(json.dumps({"own_workers_max": 3}), "utf-8")
    code, lines = run_cli(tmp_path, capsys)
    assert code == 0 and lines[0] == "workers: 3" and lines[1].startswith("run=")


def test_the_workers_line_carries_the_note_when_xdist_is_missing(tmp_path, capsys, eight_cores, settings_file,
                                                                  monkeypatch):
    monkeypatch.setattr(workers, "xdist_importable", lambda: False)
    code, lines = run_cli(tmp_path, capsys)
    assert code == 0 and lines[0] == "workers: 1 note=workers_unavailable"


@NEEDS_XDIST
def test_a_command_that_spells_its_own_workers_heads_the_output_without_a_count(tmp_path, capsys, eight_cores,
                                                                              settings_file):
    settings_file.write_text(json.dumps({"test_command": [*PYTEST, "-n", "auto"]}), "utf-8")
    code, lines = run_cli(tmp_path, capsys)
    assert lines[0] == "workers: set by the command note=workers_in_command" and lines[1].startswith("run=")


def test_a_run_with_no_workers_has_no_workers_line(tmp_path, capsys, settings_file):
    settings_file.write_text(json.dumps({"test_command": [sys.executable, "-c", "pass"]}), "utf-8")
    _, lines = run_cli(tmp_path, capsys)
    assert lines[0].startswith("run=")


# the settings


def test_the_cap_is_unset_by_default_and_read_from_the_settings():
    assert TestSettings().own_workers_max is None
    assert TestSettings.from_settings({"own_workers_max": 4}, REPO).own_workers_max == 4


@pytest.mark.parametrize("value", [0, -1, 1.5, "4", True])
def test_a_bad_cap_is_a_bad_setting(value):
    with pytest.raises(SettingsError):
        TestSettings.from_settings({"own_workers_max": value}, REPO)


def test_a_machine_may_carry_its_own_cap():
    raw = {"test_machines": [{"id": "box", "host": "fakehost", "os": LINUX_OS, "checkout": "/srv/x",
                              "workers_max": 3}]}
    [machine] = TestSettings.from_settings(raw, REPO).machines
    assert isinstance(machine, Machine) and machine.workers_max == 3
    with pytest.raises(SettingsError):
        TestSettings.from_settings({"test_machines": [{**raw["test_machines"][0], "workers_max": 0}]}, REPO)


def test_the_terminal_size_variables_of_the_caller_never_reach_the_test_process():
    """COLUMNS and LINES that show in a worker's environment are set by the worker's own C library, not passed on."""
    caller = {"PATH": "a-path", "HOME": "a-home", "COLUMNS": "80", "LINES": "24"}
    assert clean_environment({}, source=caller, os_name=LINUX_OS) == {"PATH": "a-path", "HOME": "a-home"}
