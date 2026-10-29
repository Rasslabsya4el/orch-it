"""A machine place over ssh (#394 decision 4, row 5), with a fake `ssh` that runs the remote
command locally: a run at a commit is checked out in the machine's own folder, the caller's work
tree is never touched, the result lands in the caller's store under `machine:<id>` with the
outcome the own place gives, and a place that cannot take the run or vanishes mid-run is refused
or lost (never failed) and the run goes on at the rule's next place. The real sshd is
test_machine_sshd.py's."""

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from machine_repos import (GREEN, RED, SLOW_GREEN, SLOW_RED, Repos, dead_within, git, prefer_machine,
                           sleeping_suite)

from orch_it import identity
from orch_it.events.store import Store
from orch_it.testruns import environment, machine, outcome
from orch_it.testruns.config import Machine, TestSettings
from orch_it.testruns.machine import MachinePlace
from orch_it.testruns.places import OwnPlace, PlaceRefused, PlaceVanished, Request
from orch_it.testruns.runner import default_places, run_tests

REPO = "acme-x/widgets-y"
FAKE_SSH = (sys.executable, str(Path(__file__).with_name("fake_ssh.py")))
BOX = "machine:box"
COMMAND = ("{python}", "-m", "pytest", "-q", "--junitxml={report}")


def box_machine(tmp_path, **options) -> Machine:
    return Machine("box", "fakehost", environment.this_os(), str(tmp_path / "box-checkout"),
                   python=sys.executable, **options)


def setup(tmp_path, old_test=GREEN, new_test=None, **settings_options):
    repos = Repos(tmp_path, old_test, *([new_test] if new_test else []))
    box = box_machine(tmp_path)
    settings = TestSettings(machines=(box,), **settings_options)
    places = {"own": OwnPlace(), BOX: MachinePlace(box, origin_url=str(repos.origin), ssh=FAKE_SSH)}
    return repos, box, settings, places, Store.open(tmp_path / "store")


def run_at(store, repos, settings, places, **options):
    return run_tests(store, work_tree=repos.caller, repo=REPO, settings=settings, places=places, **options)


def runs_of(store, place):
    return store.query(
        "SELECT e.outcome, e.reason, e.collected, e.failed FROM events s JOIN events e ON e.run = s.run"
        " WHERE s.kind = 'step.started' AND s.step = 'test_run' AND s.place = ?"
        " AND e.kind = 'step.ended' AND e.step = 'test_run' AND s.run NOT LIKE 'seed%'", (place,)).rows


def details(store, kind):
    return [json.loads(d) for (d,) in store.query(
        "SELECT detail FROM events WHERE kind = ? AND step = 'test_run' AND run NOT LIKE 'seed%'"
        " ORDER BY at, id", (kind,)).rows]


def a_request(repos, bound_seconds=60.0, commit=None) -> Request:
    return Request(repos.caller, commit or repos.old, COMMAND, {}, bound_seconds)


def ssh_that(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def held_ssh(tmp_path, said: str = "") -> tuple[tuple[str, ...], Path, Path]:
    """A stand-in ssh that runs `said`, then blocks until the test creates `release` (its own wait ends
    at 60 s at most), then writes `finished`. A test that finds no `finished` when its run has returned
    knows the place ended the ssh before the ssh could end on its own: no clock is read."""
    release, finished = tmp_path / "release", tmp_path / "finished"
    code = (f"import os, time\n{said}\nend = time.monotonic() + 60\n"
            f"while not os.path.exists({str(release)!r}) and time.monotonic() < end: time.sleep(0.05)\n"
            f"open({str(finished)!r}, 'w').close()\n")
    return ssh_that(code), release, finished


def place_with(repos, ssh, machine_=None, **options) -> MachinePlace:
    return MachinePlace(machine_ or Machine("box", "fakehost", "linux", "/srv/x"),
                        origin_url=str(repos.origin), ssh=ssh, **options)


def test_a_failing_gh_user_read_does_not_refuse_a_machine_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "absent" / "settings.json"))
    monkeypatch.setattr(identity, "github_repo_of", lambda origin: "acme/example")  # the origin counts as GitHub

    def gh_fails():
        raise identity.IdentityError("gh_failed", "gh api user: no answer")

    monkeypatch.setattr(identity, "current_user", gh_fails)
    repos, box, settings, places, store = setup(tmp_path)
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.place, done.outcome) == (BOX, "passed")  # the run goes on with no identity


def test_a_run_checks_its_commit_out_in_the_machine_s_own_folder(tmp_path):
    repos, box, settings, places, store = setup(tmp_path)
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.place, done.outcome) == (BOX, "passed")
    assert git(box.checkout, "rev-parse", "HEAD") == repos.old != repos.new


def test_a_second_run_reuses_the_machine_s_folder_and_moves_it_to_the_new_commit(tmp_path):
    repos, box, settings, places, store = setup(tmp_path)
    run_at(store, repos, settings, places, place=BOX)
    git(repos.caller, "checkout", "--detach", repos.new)
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("failed", "red")  # the newer commit's test is red
    assert git(box.checkout, "rev-parse", "HEAD") == repos.new


def test_the_caller_s_work_tree_is_identical_before_and_after_a_machine_run(tmp_path):
    repos, box, settings, places, store = setup(tmp_path)
    repos.make_caller_dirty()
    before = repos.caller_state()
    assert before[0]  # the caller has changes the machine must not see or touch
    done = run_at(store, repos, settings, places, place=BOX)
    assert repos.caller_state() == before
    assert done.outcome == "passed"  # the machine ran the commit, not the caller's changes


@pytest.mark.parametrize("suite, outcome_", [(GREEN, "passed"), (RED, "failed")], ids=["green", "red"])
def test_the_store_holds_the_machine_run_with_the_outcome_own_gives(tmp_path, suite, outcome_):
    repos, box, settings, places, store = setup(tmp_path, suite)
    own = run_at(store, repos, settings, places, place="own")
    far = run_at(store, repos, settings, places, place=BOX)
    assert (far.place, far.outcome, far.reason) == (BOX, outcome_, own.reason)
    assert (far.collected, far.failed) == (own.collected, own.failed)
    assert runs_of(store, BOX) == runs_of(store, "own")
    rows = store.query("SELECT s.place, r.test_id, r.outcome FROM events r JOIN events s ON s.run = r.run"
                       " AND s.kind = 'step.started' WHERE r.kind = 'test.result'").rows
    assert sorted((t, o) for p, t, o in rows if p == BOX) == sorted((t, o) for p, t, o in rows if p == "own")


def test_the_run_measures_its_start_and_its_run_time(tmp_path):
    repos, box, settings, places, store = setup(tmp_path)
    run_at(store, repos, settings, places, place=BOX)
    [(start, run)] = store.query("SELECT start_minutes, run_minutes FROM events"
                                 " WHERE kind = 'step.ended' AND step = 'test_run'").rows
    assert start > 0 and run > 0


def test_a_run_at_a_machine_records_no_dirty_flag_while_one_at_own_does(tmp_path):
    repos, box, settings, places, store = setup(tmp_path)
    (repos.caller / "scratch.txt").write_text("not committed", "utf-8")
    run_at(store, repos, settings, places, place="own")
    run_at(store, repos, settings, places, place=BOX)
    assert [(d["place"], d["dirty"]) for d in details(store, "step.started")] == [("own", True), (BOX, None)]


def test_an_ids_file_selects_the_tests_on_the_machine_as_at_own(tmp_path):
    repos, box, settings, places, store = setup(tmp_path, RED)
    ids = tmp_path / "ids.txt"
    ids.write_text("test_suite.py::test_adds\ntest_suite.py::test_missing\n", "utf-8")
    own = run_at(store, repos, settings, places, place="own", ids_file=ids)
    far = run_at(store, repos, settings, places, place=BOX, ids_file=ids)
    assert (own.outcome, own.reason) == ("no_result", "ids_not_collected")
    assert (far.outcome, far.reason, far.missing_ids) == (own.outcome, own.reason, own.missing_ids)


def test_a_run_cut_by_its_bound_on_the_machine_is_no_result_cut_and_its_test_tree_is_dead(tmp_path):
    pidfile = tmp_path / "test.pid"
    repos, box, settings, places, store = setup(tmp_path, sleeping_suite(pidfile), bound_minutes=0.1)
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("no_result", "cut")
    assert dead_within(int(pidfile.read_text()))


def test_a_command_that_writes_no_report_on_the_machine_is_no_result_summary_missing(tmp_path):
    repos, box, settings, places, store = setup(tmp_path, command=("{python}", "-c", "pass"))
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("no_result", "summary_missing")


def test_a_suite_with_no_tests_on_the_machine_is_no_result_none_collected(tmp_path):
    repos, box, settings, places, store = setup(tmp_path, "x = 1\n")
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("no_result", "none_collected")


def test_the_test_process_on_the_machine_gets_the_clean_environment_of_the_far_os(tmp_path, monkeypatch):
    suite = ("import os\n\ndef test_environment():\n"
             "    assert 'ORCH_IT_CALLER_ONLY' not in os.environ\n"
             "    assert os.environ['ORCH_IT_FAR'] == '1'\n")
    repos, box, settings, places, store = setup(tmp_path, suite, test_env={"ORCH_IT_FAR": "1"})
    monkeypatch.setenv("ORCH_IT_CALLER_ONLY", "1")  # the fake ssh hands the caller's environment on
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("passed", None)


def test_a_commit_the_machine_cannot_fetch_is_refused_place_off_and_the_rule_reruns_it_at_own(tmp_path):
    repos, box, settings, places, store = setup(tmp_path)
    (repos.caller / "extra.txt").write_text("only here", "utf-8")
    git(repos.caller, "add", "extra.txt")
    git(repos.caller, "commit", "-m", "never pushed")
    prefer_machine(store, BOX)
    done = run_at(store, repos, settings, places)
    assert (done.place, done.outcome) == ("own", "passed")
    assert runs_of(store, BOX) == [("refused", "place_off", 0, 0)]
    assert details(store, "step.ended")[0]["refused_step"] == "commit"


def test_a_dropped_connection_after_the_start_is_lost_and_the_run_goes_to_own_never_failed(
        tmp_path, monkeypatch):
    pidfile = tmp_path / "test.pid"
    repos, box, settings, places, store = setup(tmp_path, sleeping_suite(pidfile))
    prefer_machine(store, BOX)
    monkeypatch.setenv("FAKE_SSH_DROP_WHEN_FILE", str(pidfile))
    done = run_at(store, repos, settings, places)
    assert (done.place, done.outcome) == ("own", "passed")
    assert runs_of(store, BOX) == [("lost", "place_vanished", 0, 0)]
    assert store.query("SELECT COUNT(*) FROM events WHERE outcome = 'failed'").rows == [(0,)]


def keepalive_bound() -> float:
    """How long a dead connection may stand before the product's ssh gives it up: the keepalive's
    interval times its count (`machine.SSH_OPTIONS`), read from the product, not retyped here."""
    settings = dict(option.split("=", 1) for option in machine.SSH_OPTIONS[1::2])
    return float(settings["ServerAliveInterval"]) * float(settings["ServerAliveCountMax"])


def test_the_remote_test_process_is_dead_within_the_keepalive_bound_of_the_connection_dropping(
        tmp_path, monkeypatch):
    pidfile = tmp_path / "test.pid"
    repos, box, settings, places, store = setup(tmp_path, sleeping_suite(pidfile))
    monkeypatch.setenv("FAKE_SSH_DROP_WHEN_FILE", str(pidfile))
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("lost", "place_vanished")
    assert dead_within(int(pidfile.read_text()), keepalive_bound())


def test_a_report_cut_off_halfway_is_never_read_and_the_run_is_lost(tmp_path, monkeypatch):
    repos, *_ = setup(tmp_path)
    monkeypatch.setenv("FAKE_SSH_CUT_REPORT", "1")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(PlaceVanished):
        place_with(repos, FAKE_SSH, box_machine(tmp_path)).run(a_request(repos), scratch)
    assert not (scratch / "junit.xml").exists()


def test_a_banner_glued_in_front_of_the_first_line_does_not_hide_it(tmp_path, monkeypatch):
    repos, box, settings, places, store = setup(tmp_path)
    monkeypatch.setenv("FAKE_SSH_BANNER", "1")
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("passed", None)


@pytest.mark.parametrize("lines", [
    "print('ORCH-IT END {not json')",
    "print('ORCH-IT END {}'); print('ORCH-IT REPORT !!not base64!!')",
], ids=["end-not-json", "report-not-base64"])
def test_an_end_or_report_that_cannot_be_decoded_is_a_lost_run(tmp_path, lines):
    repos, *_ = setup(tmp_path)
    ssh = ssh_that(f"print('ORCH-IT START {{}}', flush=True); {lines}; import sys; sys.stdout.flush(); "
                   "import time; time.sleep(60)")
    with pytest.raises(PlaceVanished):
        place_with(repos, ssh).run(a_request(repos), tmp_path)


def test_a_protocol_line_over_the_cap_is_a_lost_run(tmp_path, monkeypatch):
    repos, *_ = setup(tmp_path)
    monkeypatch.setattr(machine, "MAX_LINE_BYTES", 1000)
    ssh, release, finished = held_ssh(tmp_path, "print('ORCH-IT START {}', flush=True)\n"
                                      "print('ORCH-IT REPORT ' + 'A' * 5000, flush=True)")
    try:
        with pytest.raises(PlaceVanished):
            place_with(repos, ssh).run(a_request(repos), tmp_path)
        assert not finished.exists()  # the run ended at the line; its ssh was still waiting
    finally:
        release.touch()


def test_an_ssh_that_cannot_connect_refuses_the_run_place_off_keeping_what_it_said(tmp_path):
    repos, *_ = setup(tmp_path)
    ssh = ssh_that("import sys; sys.stderr.write('zz-said-this'); sys.exit(255)")
    with pytest.raises(PlaceRefused) as refused:
        place_with(repos, ssh).run(a_request(repos), tmp_path)
    assert (refused.value.code, refused.value.step) == ("place_off", "connect")
    assert "zz-said-this" in str(refused.value)


def test_an_ssh_that_never_starts_the_run_in_the_setup_time_is_refused_place_off(tmp_path):
    repos, *_ = setup(tmp_path)
    ssh, release, finished = held_ssh(tmp_path)
    try:
        with pytest.raises(PlaceRefused) as refused:
            place_with(repos, ssh, setup_seconds=1.0).run(a_request(repos), tmp_path)
        assert (refused.value.code, refused.value.step) == ("place_off", "start_timeout")
        assert not finished.exists()  # refused at the setup time; its ssh was still waiting
    finally:
        release.touch()


def test_the_run_bound_counts_from_the_start_line_not_from_the_connection(tmp_path):
    repos, *_ = setup(tmp_path)
    pidfile = tmp_path / "ssh.pid"
    ssh, release, finished = held_ssh(
        tmp_path, f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                  "time.sleep(2)\nprint('ORCH-IT START {}', flush=True)")
    place = place_with(repos, ssh, setup_seconds=30.0, run_slack_seconds=0.5)
    started = time.monotonic()
    try:
        with pytest.raises(PlaceVanished):
            place.run(a_request(repos, bound_seconds=1.0), tmp_path)
        assert 3 <= time.monotonic() - started  # 2 s to the start line, then bound and slack; a floor only
        assert not finished.exists()  # the bound ended the ssh; it did not wait for the ssh's own end
        assert dead_within(int(pidfile.read_text()))
    finally:
        release.touch()


def test_a_machine_with_no_commit_to_check_out_is_refused_place_off(tmp_path):
    repos, *_ = setup(tmp_path)
    request = Request(repos.caller, None, ("{python}", "-m", "pytest"), {}, 60.0)
    with pytest.raises(PlaceRefused) as refused:
        place_with(repos, FAKE_SSH).run(request, tmp_path)
    assert refused.value.code == "place_off"


def test_a_machine_with_no_origin_to_fetch_from_is_refused_place_off(tmp_path):
    repos, *_ = setup(tmp_path)
    place = MachinePlace(Machine("box", "fakehost", "linux", "/srv/x"), origin_url=None, ssh=FAKE_SSH)
    with pytest.raises(PlaceRefused) as refused:
        place.run(a_request(repos), tmp_path)
    assert refused.value.code == "place_off"


def request_sent(tmp_path, place) -> dict:
    """The request line `place` sends: its ssh is a stand-in that keeps the second stdin line."""

    return json.loads((tmp_path / "request.json").read_text())


def recording_ssh(tmp_path) -> tuple[str, ...]:
    return ssh_that("import sys; b = sys.stdin.buffer; b.readline();"
                    f" open({str(tmp_path / 'request.json')!r}, 'wb').write(b.readline()); sys.exit(255)")


def test_a_password_in_the_callers_http_origin_is_not_sent_to_the_machine(tmp_path):
    repos, *_ = setup(tmp_path)
    place = MachinePlace(Machine("box", "fakehost", "linux", "/srv/x"), ssh=recording_ssh(tmp_path),
                         origin_url="https://me:secret@example.org:8443/me/x.git")
    with pytest.raises(PlaceRefused):
        place.run(a_request(repos), tmp_path)
    assert request_sent(tmp_path, place)["origin"] == "https://example.org:8443/me/x.git"


def test_an_origin_setting_of_the_machine_is_sent_as_given(tmp_path):
    repos, *_ = setup(tmp_path)
    given = "https://me:token@example.org/me/x.git"
    place = MachinePlace(Machine("box", "fakehost", "linux", "/srv/x", origin=given),
                         ssh=recording_ssh(tmp_path), origin_url="https://example.org/other.git")
    with pytest.raises(PlaceRefused):
        place.run(a_request(repos), tmp_path)
    assert request_sent(tmp_path, place)["origin"] == given


def test_ssh_is_asked_once_with_the_users_options_first_then_batch_mode_and_alive_checks(tmp_path):
    repos, *_ = setup(tmp_path)
    record = tmp_path / "argv.txt"
    ssh = ssh_that(f"import sys; open({str(record)!r}, 'w').write(chr(10).join(sys.argv[1:])); sys.exit(255)")
    far = Machine("box", "me@fakehost", "linux", "/srv/x", python="/opt/py/bin/python3",
                  ssh_options=("-p", "2222"))
    with pytest.raises(PlaceRefused):
        place_with(repos, ssh, far).run(a_request(repos), tmp_path)
    argv = record.read_text().split("\n")
    assert argv[argv.index("-p") + 1] == "2222"
    for option in ("BatchMode=yes", "ConnectTimeout=20", "ServerAliveInterval=15", "ServerAliveCountMax=4"):
        assert argv[argv.index(option) - 1] == "-o"
        assert argv.index("-p") < argv.index(option)  # ssh takes the first value of an option
    assert argv[-3:-1] == ["--", "me@fakehost"]
    assert argv[-1] == '/opt/py/bin/python3 -c "import sys;exec(sys.stdin.readline())"'


@pytest.mark.parametrize("os_name, python, command", [
    ("windows", "python", '"python -c "import sys;exec(sys.stdin.readline())""'),
    ("windows", r"C:\Program Files\Py\python.exe",
     '""C:\\Program Files\\Py\\python.exe" -c "import sys;exec(sys.stdin.readline())""'),
    ("linux", None, 'python3 -c "import sys;exec(sys.stdin.readline())"'),
    ("macos", "/usr/local/bin/my python", '"/usr/local/bin/my python" -c "import sys;exec(sys.stdin.readline())"'),
], ids=["windows", "windows-spaces", "linux", "macos-spaces"])
def test_the_remote_command_suits_the_far_shell(os_name, python, command):
    far = Machine("box", "fakehost", os_name, "/srv/x", python=python)
    assert MachinePlace(far).remote_command() == command


def test_the_default_places_are_own_and_one_machine_place_per_setting_with_the_callers_origin(tmp_path):
    repos = Repos(tmp_path)
    settings = TestSettings(machines=(Machine("box", "box", "linux", "/x"),
                                      Machine("win", "win", "windows", "C:/x", origin="https://example.org/x.git")))
    places = default_places(settings, repos.caller)
    assert list(places) == ["own", "machine:box", "machine:win"]
    assert places["machine:box"].origin_url == str(repos.origin)
    assert places["machine:win"].machine.origin == "https://example.org/x.git"


def test_a_run_can_be_forced_to_a_machine_of_the_settings_without_handing_places_in(tmp_path):
    repos = Repos(tmp_path)
    far = Machine("box", "127.0.0.1", environment.this_os(), str(tmp_path / "co"), ssh_options=("-p", "1"))
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=repos.caller, repo=REPO, settings=TestSettings(machines=(far,)),
                     place=BOX)
    assert (done.place, done.outcome, done.reason) == (BOX, "refused", "place_off")


def lock_of(box: Machine, slot: int = 0) -> Path:
    return Path(f"{box.checkout}.orch-it-lock" if slot == 0 else f"{box.checkout}-{slot}.orch-it-lock")


def test_a_run_holds_its_slot_by_a_lock_file_and_lets_it_go_at_the_end(tmp_path):
    suite = ("import os\n\ndef test_lock_is_held():\n"
             f"    assert os.path.exists({str(tmp_path / 'box-checkout.orch-it-lock')!r})\n")
    repos, box, settings, places, store = setup(tmp_path, suite)
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("passed", None)
    assert not lock_of(box).exists()


def test_a_machine_whose_every_slot_is_locked_is_refused_busy(tmp_path):
    repos, box, settings, places, store = setup(tmp_path)
    lock_of(box).write_text("{}", "utf-8")
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("refused", "busy")
    assert details(store, "step.ended")[0]["refused_step"] == "busy"
    assert lock_of(box).exists()  # a lock that is not ours is never let go
    assert not Path(box.checkout).exists()


def test_a_lock_older_than_the_bound_and_the_setup_time_is_stale_and_broken(tmp_path):
    repos, box, settings, places, store = setup(tmp_path)
    lock = lock_of(box)
    lock.write_text("{}", "utf-8")
    long_ago = time.time() - 4 * 3600
    os.utime(lock, (long_ago, long_ago))
    done = run_at(store, repos, settings, places, place=BOX)
    assert (done.outcome, done.reason) == ("passed", None)
    assert not lock.exists()


def test_two_concurrent_runs_at_different_commits_each_see_their_own_commit(tmp_path):
    repos = Repos(tmp_path, SLOW_GREEN, SLOW_RED)
    box = box_machine(tmp_path, capacity=2)
    place = MachinePlace(box, origin_url=str(repos.origin), ssh=FAKE_SSH)
    results = {}

    def run(name, commit):
        scratch = tmp_path / f"scratch-{name}"
        scratch.mkdir()
        end = place.run(a_request(repos, commit=commit), scratch)
        results[name] = outcome.judge(end.report).outcome

    threads = [threading.Thread(target=run, args=("old", repos.old)),
               threading.Thread(target=run, args=("new", repos.new))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert results == {"old": "passed", "new": "failed"}
    assert sorted([git(box.checkout, "rev-parse", "HEAD"), git(f"{box.checkout}-1", "rev-parse", "HEAD")]) \
        == sorted([repos.old, repos.new])


def test_a_folder_that_is_not_the_layer_s_own_checkout_is_refused_and_left_as_it_is(tmp_path):
    repos = Repos(tmp_path)
    repos.make_caller_dirty()
    before = repos.caller_state()
    far = Machine("box", "fakehost", environment.this_os(), str(repos.caller), python=sys.executable)
    with pytest.raises(PlaceRefused) as refused:
        place_with(repos, FAKE_SSH, far).run(a_request(repos), tmp_path)
    assert (refused.value.code, refused.value.step) == ("place_off", "not_ours")
    assert repos.caller_state() == before
    assert (repos.caller / "scratch.txt").read_text("utf-8") == "not committed"


def test_a_checkout_of_the_layer_that_is_no_repository_any_more_is_cloned_again_and_what_encloses_it_is_left(
        tmp_path):
    repos = Repos(tmp_path)
    inner = repos.caller / "machine-checkout"  # inside the caller's work tree, so git finds that one
    far = Machine("box", "fakehost", environment.this_os(), str(inner), python=sys.executable)
    place = place_with(repos, FAKE_SSH, far)
    place.run(a_request(repos), tmp_path)
    (inner / ".git" / "HEAD").unlink()  # broken: git now reads the enclosing work tree instead
    (repos.caller / "scratch.txt").write_text("not committed", "utf-8")
    before = repos.caller_state()
    end = place.run(a_request(repos), tmp_path)
    assert end.report is not None
    assert git(inner, "rev-parse", "HEAD") == repos.old
    assert (git(repos.caller, "status", "--porcelain").replace("?? machine-checkout/\n", ""),
            git(repos.caller, "rev-parse", "HEAD")) == (before[0].replace("?? machine-checkout/\n", ""), before[1])
    assert (repos.caller / "scratch.txt").read_text("utf-8") == "not committed"
    shutil.rmtree(inner, ignore_errors=True)
