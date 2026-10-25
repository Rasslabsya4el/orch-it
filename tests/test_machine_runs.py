"""A machine place over ssh (#394 decision 4, row 5), with a fake `ssh` that runs the remote
command locally: a run at a commit is checked out in the machine's own folder, the caller's work
tree is never touched, the result lands in the caller's store under `machine:<id>` with the
outcome the own place gives, and a place that cannot take the run or vanishes mid-run is refused
or lost (never failed) and the run goes on at the rule's next place. The real sshd is
test_machine_sshd.py's."""

import sys
import time
from pathlib import Path

import pytest
from machine_repos import GREEN, RED, Repos, alive, git, prefer_machine, sleeping_suite

from orch_it.events.store import Store
from orch_it.testruns import environment
from orch_it.testruns.config import Machine, TestSettings
from orch_it.testruns.machine import MachinePlace
from orch_it.testruns.places import OwnPlace, PlaceRefused, PlaceVanished, Request
from orch_it.testruns.runner import run_tests

REPO = "Rasslabsya4el/orch-it"
FAKE_SSH = (sys.executable, str(Path(__file__).with_name("fake_ssh.py")))
BOX = "machine:box"


def box_machine(tmp_path) -> Machine:
    return Machine("box", "fakehost", environment.this_os(), str(tmp_path / "box-checkout"),
                   python=sys.executable)


def setup(tmp_path, old_test=GREEN):
    repos = Repos(tmp_path, old_test)
    machine = box_machine(tmp_path)
    settings = TestSettings(machines=(machine,))
    places = {"own": OwnPlace(), BOX: MachinePlace(machine, origin_url=str(repos.origin), ssh=FAKE_SSH)}
    return repos, machine, settings, places, Store.open(tmp_path / "store")


def run_at(store, repos, settings, places, **options):
    return run_tests(store, work_tree=repos.caller, repo=REPO, settings=settings, places=places, **options)


def runs_of(store, place):
    return store.query(
        "SELECT e.outcome, e.reason, e.collected, e.failed FROM events s JOIN events e ON e.run = s.run"
        " WHERE s.kind = 'step.started' AND s.step = 'test_run' AND s.place = ?"
        " AND e.kind = 'step.ended' AND e.step = 'test_run' AND s.run NOT LIKE 'seed%'", (place,)).rows


def test_a_run_checks_its_commit_out_in_the_machine_s_own_folder(tmp_path):
    repos, machine, settings, places, store = setup(tmp_path)
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.place, done.outcome) == (BOX, "passed")
    assert git(machine.checkout, "rev-parse", "HEAD") == repos.old != repos.new


def test_a_second_run_reuses_the_machine_s_folder_and_moves_it_to_the_new_commit(tmp_path):
    repos, machine, settings, places, store = setup(tmp_path)
    run_at(store, repos, settings, places, place=BOX)
    git(repos.caller, "checkout", "--detach", repos.new)
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("failed", "red")  # the newer commit's test is red
    assert git(machine.checkout, "rev-parse", "HEAD") == repos.new


def test_the_caller_s_work_tree_is_identical_before_and_after_a_machine_run(tmp_path):
    repos, machine, settings, places, store = setup(tmp_path)
    repos.make_caller_dirty()
    before = repos.caller_state()
    assert before[0]  # the caller has changes the machine must not see or touch
    done = run_at(store, repos, settings, places, place=BOX)
    assert repos.caller_state() == before
    assert done.outcome == "passed"  # the machine ran the commit, not the caller's changes


@pytest.mark.parametrize("suite, outcome", [(GREEN, "passed"), (RED, "failed")], ids=["green", "red"])
def test_the_store_holds_the_machine_run_with_the_outcome_own_gives(tmp_path, suite, outcome):
    repos, machine, settings, places, store = setup(tmp_path, suite)
    own = run_at(store, repos, settings, places, place="own")
    box = run_at(store, repos, settings, places, place=BOX)
    assert (box.place, box.outcome, box.reason) == (BOX, outcome, own.reason)
    assert (box.collected, box.failed) == (own.collected, own.failed)
    assert runs_of(store, BOX) == runs_of(store, "own")
    rows = store.query("SELECT s.place, r.test_id, r.outcome FROM events r JOIN events s ON s.run = r.run"
                       " AND s.kind = 'step.started' WHERE r.kind = 'test.result'").rows
    assert sorted((t, o) for p, t, o in rows if p == BOX) == sorted((t, o) for p, t, o in rows if p == "own")


def test_the_run_measures_its_start_and_its_run_time(tmp_path):
    repos, machine, settings, places, store = setup(tmp_path)
    run_at(store, repos, settings, places, place=BOX)
    [(start, run)] = store.query("SELECT start_minutes, run_minutes FROM events"
                                 " WHERE kind = 'step.ended' AND step = 'test_run'").rows
    assert start > 0 and run > 0


def test_an_ids_file_selects_the_tests_on_the_machine_as_at_own(tmp_path):
    repos, machine, settings, places, store = setup(tmp_path, RED)
    ids = tmp_path / "ids.txt"
    ids.write_text("test_suite.py::test_adds\ntest_suite.py::test_missing\n", "utf-8")
    own = run_at(store, repos, settings, places, place="own", ids_file=ids)
    box = run_at(store, repos, settings, places, place=BOX, ids_file=ids)
    assert (own.outcome, own.reason) == ("no_result", "ids_not_collected")
    assert (box.outcome, box.reason, box.missing_ids) == (own.outcome, own.reason, own.missing_ids)


def test_a_commit_the_machine_cannot_fetch_is_refused_place_off_and_the_rule_reruns_it_at_own(tmp_path):
    repos, machine, settings, places, store = setup(tmp_path)
    (repos.caller / "extra.txt").write_text("only here", "utf-8")
    git(repos.caller, "add", "extra.txt")
    git(repos.caller, "commit", "-m", "never pushed")
    prefer_machine(store, BOX)
    done = run_at(store, repos, settings, places)
    assert (done.place, done.outcome) == ("own", "passed")
    assert runs_of(store, BOX) == [("refused", "place_off", 0, 0)]


def test_a_dropped_connection_after_the_start_is_lost_and_the_run_goes_to_own_never_failed(
        tmp_path, monkeypatch):
    pidfile = tmp_path / "test.pid"
    repos, machine, settings, places, store = setup(tmp_path, sleeping_suite(pidfile))
    prefer_machine(store, BOX)
    monkeypatch.setenv("FAKE_SSH_DROP_WHEN_FILE", str(pidfile))
    done = run_at(store, repos, settings, places)
    assert (done.place, done.outcome) == ("own", "passed")
    assert runs_of(store, BOX) == [("lost", "place_vanished", 0, 0)]
    assert store.query("SELECT COUNT(*) FROM events WHERE outcome = 'failed'").rows == [(0,)]


def test_the_remote_test_process_is_dead_within_15_seconds_of_the_connection_dropping(
        tmp_path, monkeypatch):
    pidfile = tmp_path / "test.pid"
    repos, machine, settings, places, store = setup(tmp_path, sleeping_suite(pidfile))
    monkeypatch.setenv("FAKE_SSH_DROP_WHEN_FILE", str(pidfile))
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("lost", "place_vanished")
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 15
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    try:
        assert not alive(pid)
    finally:
        if alive(pid):  # never leave the sleeper behind, whatever the verdict
            import os
            import signal
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def ssh_that(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def a_request(repos, bound_seconds=60.0) -> Request:
    return Request(repos.caller, repos.old, ("{python}", "-m", "pytest", "-q", "--junitxml={report}"), {},
                   bound_seconds)


def test_an_ssh_that_cannot_connect_refuses_the_run_place_off_saying_why(tmp_path):
    repos, machine, *_ = setup(tmp_path)
    ssh = ssh_that("import sys; sys.stderr.write('ssh: connect to host box port 22: Connection refused');"
                   " sys.exit(255)")
    place = MachinePlace(machine, origin_url=str(repos.origin), ssh=ssh)
    with pytest.raises(PlaceRefused) as refused:
        place.run(a_request(repos), tmp_path)
    assert refused.value.code == "place_off" and "Connection refused" in str(refused.value)


def test_a_machine_with_no_commit_to_check_out_is_refused_place_off(tmp_path):
    repos, machine, *_ = setup(tmp_path)
    place = MachinePlace(machine, origin_url=str(repos.origin), ssh=FAKE_SSH)
    request = Request(repos.caller, None, ("{python}", "-m", "pytest"), {}, 60.0)
    with pytest.raises(PlaceRefused) as refused:
        place.run(request, tmp_path)
    assert refused.value.code == "place_off"


def test_a_machine_with_no_origin_to_fetch_from_is_refused_place_off(tmp_path):
    repos, machine, *_ = setup(tmp_path)
    place = MachinePlace(machine, origin_url=None, ssh=FAKE_SSH)
    with pytest.raises(PlaceRefused) as refused:
        place.run(a_request(repos), tmp_path)
    assert refused.value.code == "place_off"


def test_the_local_bound_passing_after_the_start_kills_ssh_and_the_run_is_lost(tmp_path):
    repos, machine, *_ = setup(tmp_path)
    pidfile = tmp_path / "ssh.pid"
    ssh = ssh_that(f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid()));"
                   " print('ORCH-IT START {}', flush=True); time.sleep(120)")
    place = MachinePlace(machine, origin_url=str(repos.origin), ssh=ssh, setup_seconds=1.0)
    started = time.monotonic()
    with pytest.raises(PlaceVanished):
        place.run(a_request(repos, bound_seconds=1.0), tmp_path)
    assert time.monotonic() - started < 30
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 15
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(pid)


def test_ssh_is_asked_once_with_batch_mode_and_alive_checks_and_the_machine_s_python(tmp_path):
    repos, machine, *_ = setup(tmp_path)
    record = tmp_path / "argv.txt"
    ssh = ssh_that(f"import sys; open({str(record)!r}, 'w').write(chr(10).join(sys.argv[1:])); sys.exit(255)")
    machine = Machine("box", "me@fakehost", "linux", "/srv/x", python="/opt/py/bin/python3",
                      ssh_options=("-p", "2222"))
    place = MachinePlace(machine, origin_url=str(repos.origin), ssh=ssh)
    with pytest.raises(PlaceRefused):
        place.run(a_request(repos), tmp_path)
    argv = record.read_text().split("\n")
    assert argv[-2] == "me@fakehost"
    assert argv[argv.index("-p") + 1] == "2222"
    for option in ("BatchMode=yes", "ConnectTimeout=20", "ServerAliveInterval=15", "ServerAliveCountMax=4"):
        assert argv[argv.index(option) - 1] == "-o"
    assert argv[-1] == '/opt/py/bin/python3 -c "import sys;exec(sys.stdin.readline())"'


@pytest.mark.parametrize("os_name, python", [("windows", "python"), ("linux", "python3"), ("macos", "python3")])
def test_the_remote_python_defaults_to_the_far_os_s_name_for_it(tmp_path, os_name, python):
    repos, *_ = setup(tmp_path)
    record = tmp_path / "argv.txt"
    ssh = ssh_that(f"import sys; open({str(record)!r}, 'w').write(sys.argv[-1]); sys.exit(255)")
    machine = Machine("box", "fakehost", os_name, "/srv/x")
    with pytest.raises(PlaceRefused):
        MachinePlace(machine, origin_url=str(repos.origin), ssh=ssh).run(a_request(repos), tmp_path)
    assert record.read_text() == python + ' -c "import sys;exec(sys.stdin.readline())"'


def test_the_default_places_are_own_and_one_machine_place_per_setting_with_the_callers_origin(tmp_path):
    from orch_it.testruns.runner import default_places
    repos = Repos(tmp_path)
    settings = TestSettings(machines=(Machine("box", "box", "linux", "/x"),
                                      Machine("win", "win", "windows", "C:/x", origin="https://example.org/x.git")))
    places = default_places(settings, repos.caller)
    assert list(places) == ["own", "machine:box", "machine:win"]
    assert places["machine:box"].origin_url == str(repos.origin)
    assert places["machine:win"].machine.origin == "https://example.org/x.git"


def test_a_run_can_be_forced_to_a_machine_of_the_settings_without_handing_places_in(tmp_path):
    repos = Repos(tmp_path)
    machine = Machine("box", "127.0.0.1", environment.this_os(), str(tmp_path / "co"), ssh_options=("-p", "1"))
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=repos.caller, repo=REPO, settings=TestSettings(machines=(machine,)),
                     place=BOX)
    assert (done.place, done.outcome, done.reason) == (BOX, "refused", "place_off")
