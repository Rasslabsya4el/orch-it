"""A machine place over a real ssh and a real sshd (#394 acceptance row 5, the `linux` job): an
sshd started as the current user on localhost is the machine `local-ssh`. A run at a given commit
is checked out in that machine's folder, the caller's work tree is left as it was, and the result
lands in the caller's store under `machine:local-ssh` with the outcome own gives. With the sshd
killed mid-run the run is lost `place_vanished`, the rule's next place reruns it, and no
`failed` is written.

Skipped when `sshd`, `ssh` or `ssh-keygen` is missing; with ORCH_IT_REQUIRE_SSHD=1 it fails
instead (the `linux` job sets it, so a runner without sshd cannot pass this row by skipping)."""

import faulthandler
import getpass
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from machine_repos import GREEN, RED, Repos, dead_within, git, prefer_machine, sleeping_suite

from orch_it.events.store import Store
from orch_it.testruns.config import Machine, TestSettings
from orch_it.testruns.machine import REMOTE_PROGRAM, MachinePlace
from orch_it.testruns.places import OwnPlace
from orch_it.testruns.runner import run_tests

REPO = "acme-x/widgets-y"
PLACE = "machine:local-ssh"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BOUND_MINUTES = 1  # a run of these tests is short; a stuck one is cut well inside the job's time
SETUP_SECONDS = 60.0
SLACK_SECONDS = 20.0
HANG_GUARD_SECONDS = 240  # a test still going then dumps every thread's stack and ends the run
SSHD_START_TRIES = 10  # ports tried for one sshd: a port taken by another process before sshd binds it is skipped


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture(autouse=True)
def hang_guard():
    faulthandler.dump_traceback_later(HANG_GUARD_SECONDS, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


def need_sshd() -> str:
    """The sshd path, or a skip (a failure when ORCH_IT_REQUIRE_SSHD=1) when the tools are missing."""

    sshd = shutil.which("sshd") or ("/usr/sbin/sshd" if os.path.exists("/usr/sbin/sshd") else None)
    missing = [name for name, path in (("sshd", sshd), ("ssh", shutil.which("ssh")),
                                       ("ssh-keygen", shutil.which("ssh-keygen"))) if not path]
    if sys.platform == "win32":
        missing.append("a POSIX sshd")
    if missing:
        message = f"missing: {', '.join(missing)}"
        if os.environ.get("ORCH_IT_REQUIRE_SSHD") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return sshd


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def processes():
    """(pid, parent pid, command line) of every process, from /proc."""

    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        found.append((int(entry.name), int(stat[stat.rindex(")") + 2:].split()[1]), command))
    return found


def below(roots, table):
    """`roots` and every process under them."""

    found = list(roots)
    while grown := [pid for pid, parent, _ in table if parent in found and pid not in found]:
        found += grown
    return found


def connection_victims(sshd_pid: int, table) -> list[int]:
    """What a kill of this sshd takes: sshd and every process below it, except the far side of its
    connections: the processes running REMOTE_PROGRAM below sshd, and all they started. The whole
    program text is matched, because execnet's bootstrap (`import sys;exec(eval(sys.stdin.readline()))`,
    the pytest-xdist workers) does not contain it; a process above sshd is never spared for being above."""

    under = below([sshd_pid], table)
    far = below([pid for pid, _, command in table if pid in under and REMOTE_PROGRAM in command], table)
    return [pid for pid in under if pid not in far]


def keygen(path: Path):
    subprocess.run([shutil.which("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
                   check=True, capture_output=True, timeout=60, creationflags=NO_WINDOW)


def wait_listening(process, log: Path, port: int) -> bool:
    """Whether sshd's log says it listens on `port` before the process exits. sshd writes
    `Server listening on 127.0.0.1 port N.` only once its bind succeeded, so a port some other
    process holds never passes for ours."""

    marker = f"listening on 127.0.0.1 port {port}."
    deadline = time.monotonic() + 20
    while True:
        exited = process.poll() is not None
        text = log.read_text("utf-8", "replace") if log.exists() else ""
        if marker in text:
            return True
        if exited or time.monotonic() > deadline:
            return False
        time.sleep(0.1)


def launch(sshd: list[str], folder: Path, ports, config) -> tuple[subprocess.Popen, int]:
    """sshd on the first of `ports` it can bind, its config `config(port)` written into `folder`.
    The probe of `free_port` closes its socket before sshd binds, so another process may take the
    port in between: sshd then exits with `Bind to port ... failed`, and the next port is tried."""

    log = folder / "sshd.log"
    for port in ports:
        (folder / "sshd_config").write_text(config(port), "utf-8")
        with open(log, "wb") as out:
            process = subprocess.Popen([*sshd, "-D", "-e", "-p", str(port), "-f", str(folder / "sshd_config")],
                                       stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                       start_new_session=True)
        if wait_listening(process, log, port):
            return process, port
        if process.poll() is None:
            process.kill()
        process.wait()
        text = log.read_text("utf-8", "replace")
        if "Bind to port" not in text:
            pytest.fail(f"sshd did not listen on {port} ({process.returncode}): {text}")
    pytest.fail(f"sshd found no free port in {SSHD_START_TRIES} tries: {log.read_text('utf-8', 'replace')}")


class Sshd:
    """An sshd of the current user on a free localhost port, in its own process group."""

    def __init__(self, tmp_path: Path):
        sshd = need_sshd()
        folder = tmp_path / "sshd"
        folder.mkdir()
        keygen(folder / "host_key")
        keygen(folder / "client_key")
        authorized = folder / "authorized_keys"
        shutil.copy(folder / "client_key.pub", authorized)
        authorized.chmod(0o600)
        self.client_key = folder / "client_key"

        def config(port: int) -> str:
            return "\n".join([
                f"Port {port}", "ListenAddress 127.0.0.1", f"HostKey {folder / 'host_key'}",
                f"PidFile {folder / 'sshd.pid'}", f"AuthorizedKeysFile {authorized}", "StrictModes no",
                "UsePAM no", "PasswordAuthentication no", "KbdInteractiveAuthentication no",
                "PubkeyAuthentication yes", "AuthenticationMethods publickey", ""])

        self.process, self.port = launch([sshd], folder, (free_port() for _ in range(SSHD_START_TRIES)), config)

    def kill_group(self) -> int:
        """Kill sshd and its per-connection processes, whatever they are named (`sshd: user [priv]`,
        `sshd-session`, `sshd-auth` ...); return how many it killed. They run in sessions of their
        own, outside sshd's process group, so they are found through the parent chain. The command
        the connection started (the test machine's far side: the python that reads its program
        from stdin, and what it started) is spared: it must notice by itself that the connection
        is gone."""

        victims = connection_victims(self.process.pid, processes())
        for pid in reversed(victims):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait()
        return len(victims)

    def machine(self, checkout: Path) -> Machine:
        return Machine("local-ssh", f"{getpass.getuser()}@127.0.0.1", "linux", str(checkout),
                       python=sys.executable,
                       ssh_options=("-p", str(self.port), "-i", str(self.client_key),
                                    "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
                                    "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"))


@pytest.fixture
def sshd(tmp_path):
    server = Sshd(tmp_path)
    try:
        yield server
    finally:
        server.kill_group()


def setup(tmp_path, sshd, old_test):
    repos = Repos(tmp_path, old_test)
    machine = sshd.machine(tmp_path / "local-ssh-checkout")
    settings = TestSettings(machines=(machine,), bound_minutes=BOUND_MINUTES)
    places = {"own": OwnPlace(), PLACE: MachinePlace(machine, origin_url=str(repos.origin),
                                                     setup_seconds=SETUP_SECONDS,
                                                     run_slack_seconds=SLACK_SECONDS)}
    return repos, machine, settings, places, Store.open(tmp_path / "store")


def run_at(store, repos, settings, places, **options):
    return run_tests(store, work_tree=repos.caller, repo=REPO, settings=settings, places=places, **options)


def ended_runs(store, place):
    return store.query(
        "SELECT e.outcome, e.reason, e.collected, e.failed FROM events s JOIN events e ON e.run = s.run"
        " WHERE s.kind = 'step.started' AND s.step = 'test_run' AND s.place = ?"
        " AND e.kind = 'step.ended' AND e.step = 'test_run' AND s.run NOT LIKE 'seed%'", (place,)).rows


@pytest.mark.parametrize("suite, outcome", [(GREEN, "passed"), (RED, "failed")], ids=["green", "red"])
def test_a_run_over_a_real_sshd_checks_the_commit_out_and_lands_with_the_outcome_own_gives(
        tmp_path, sshd, suite, outcome):
    repos, machine, settings, places, store = setup(tmp_path, sshd, suite)
    (repos.caller / "scratch.txt").write_text("not committed", "utf-8")
    own = run_at(store, repos, settings, places, place="own")
    before = repos.caller_state()  # own ran in the work tree (and left its cache there)
    assert before[0]  # the caller has changes the machine must not touch
    done = run_at(store, repos, settings, places, place=PLACE)
    assert (done.place, done.outcome, done.reason) == (PLACE, outcome, own.reason)
    assert (done.collected, done.failed) == (own.collected, own.failed)
    assert git(machine.checkout, "rev-parse", "HEAD") == repos.old != repos.new
    assert repos.caller_state() == before
    assert ended_runs(store, PLACE) == ended_runs(store, "own")


@pytest.fixture
def killed(tmp_path, sshd):
    """One run whose sshd (with its connection children) is killed once the test has started."""

    pidfile = tmp_path / "test.pid"
    repos, machine, settings, places, store = setup(tmp_path, sshd, sleeping_suite(pidfile))
    prefer_machine(store, PLACE)

    killed_count = []

    def kill_when_started():  # the test writes its pid file once the run has started
        deadline = time.monotonic() + 60
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        killed_count.append(sshd.kill_group())

    killer = threading.Thread(target=kill_when_started, daemon=True)
    killer.start()
    try:
        done = run_at(store, repos, settings, places)
        killer.join(timeout=30)
        assert killed_count and killed_count[0] > 1, "the kill found no connection process of sshd"
        yield store, done, int(pidfile.read_text())
    finally:  # never leave the sleeping test behind, whatever the verdict
        if pidfile.exists() and pidfile.read_text().strip():
            dead_within(int(pidfile.read_text()), 0)


def test_the_sshd_killed_mid_run_is_lost_place_vanished_and_own_reruns_it(killed):
    store, done, _ = killed
    assert (done.place, done.outcome) == ("own", "passed")
    assert ended_runs(store, PLACE) == [("lost", "place_vanished", 0, 0)]


def test_the_sshd_killed_mid_run_writes_no_failed_run(killed):
    store, _, _ = killed
    assert store.query("SELECT COUNT(*) FROM events WHERE outcome = 'failed'").rows == [(0,)]


def test_the_test_process_on_the_machine_is_dead_within_15_seconds_of_the_sshd_dying(killed):
    _, _, pid = killed
    assert dead_within(pid)


# The pure checks below run on every machine, sshd or not (#421 rule 2).

XDIST_WORKER = "py-demo -c import sys;exec(eval(sys.stdin.readline()))"  # execnet gateway_io.py:51
FAR_PROGRAM = f"py-demo -c {REMOTE_PROGRAM}"  # the far side's program, as /proc shows it
PROCESSES = [  # (pid, parent pid, command line)
    (1, 0, "init-demo"),
    (100, 1, "py-demo -m pytest tests -n 4"),
    (110, 100, XDIST_WORKER),  # a worker of this run; it starts sshd, so sshd is below it
    (111, 100, XDIST_WORKER),  # another worker, not under sshd: never a victim
    (112, 111, "py-demo -c pass"),
    (200, 110, "sshd-demo -D -e -p 40123"),
    (300, 200, "sshd-session: user [priv]"),
    (301, 300, "sshd-session: user@notty"),
    (302, 301, f"sh -c {FAR_PROGRAM}"),
    (303, 302, FAR_PROGRAM),  # the far side: spared with all it started
    (304, 303, "py-demo -c import time;time.sleep(60)"),
    (305, 303, XDIST_WORKER),  # an xdist worker of the far side's pytest: spared with it
]


def test_the_kill_takes_sshd_and_its_connections_and_spares_only_the_far_side():
    assert sorted(connection_victims(200, PROCESSES)) == [200, 300, 301]


def test_a_far_side_program_above_sshd_does_not_spare_sshd():
    table = [(1, 0, "init-demo"), (9, 1, FAR_PROGRAM), (200, 9, "sshd-demo -D -e -p 40123")]
    assert sorted(connection_victims(200, table)) == [200]


FAKE_SSHD = '''
import socket, sys, time
port = int(sys.argv[sys.argv.index("-p") + 1])
listener = socket.socket()
try:
    listener.bind(("127.0.0.1", port))
except OSError as error:
    print(f"error: Bind to port {port} on 127.0.0.1 failed: {error.strerror}.", file=sys.stderr, flush=True)
    print("fatal: Cannot bind any address.", file=sys.stderr, flush=True)
    sys.exit(1)
listener.listen()
print(f"Server listening on 127.0.0.1 port {port}.", file=sys.stderr, flush=True)
time.sleep(60)
'''


def test_a_port_taken_before_sshd_binds_is_tried_again_on_the_next_port(tmp_path):
    script = tmp_path / "fake_sshd.py"
    script.write_text(FAKE_SSHD, "utf-8")
    folder = tmp_path / "sshd"
    folder.mkdir()
    taken = socket.socket()  # another process's listener, taken between the probe and sshd's bind
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    free = free_port()
    process = None
    try:
        process, port = launch([sys.executable, str(script)], folder,
                               iter([taken.getsockname()[1], free]), lambda port: "")
        assert port == free
        assert process.poll() is None
    finally:
        taken.close()
        if process is not None:
            process.kill()
            process.wait()
