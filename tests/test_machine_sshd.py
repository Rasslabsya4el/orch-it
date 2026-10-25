"""A machine place over a real ssh and a real sshd (#394 acceptance row 5, the `linux` job): an
sshd started as the current user on localhost is the machine `local-ssh`. A run at a given commit
is checked out in that machine's folder, the caller's work tree is left as it was, and the result
lands in the caller's store under `machine:local-ssh` with the outcome own gives. With the sshd
killed mid-run the run is lost `place_vanished`, the rule's next place reruns it, and no
`failed` is written.

Skipped when `sshd`, `ssh` or `ssh-keygen` is missing; with ORCH_IT_REQUIRE_SSHD=1 it fails
instead (the `linux` job sets it, so a runner without sshd cannot pass this row by skipping)."""

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
from machine_repos import GREEN, RED, Repos, alive, git, prefer_machine, sleeping_suite

from orch_it.events.store import Store
from orch_it.testruns.config import Machine, TestSettings
from orch_it.testruns.machine import MachinePlace
from orch_it.testruns.places import OwnPlace
from orch_it.testruns.runner import run_tests

REPO = "Rasslabsya4el/orch-it"
PLACE = "machine:local-ssh"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
    """(pid, parent pid, command name) of every process, in pid order, from /proc."""

    found = []
    for entry in sorted(Path("/proc").iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        name = stat[stat.index("(") + 1:stat.rindex(")")]
        found.append((int(entry.name), int(stat[stat.rindex(")") + 2:].split()[1]), name))
    return found


def keygen(path: Path):
    subprocess.run([shutil.which("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
                   check=True, capture_output=True, timeout=60, creationflags=NO_WINDOW)


class Sshd:
    """An sshd of the current user on a free localhost port, in its own process group."""

    def __init__(self, tmp_path: Path):
        sshd = need_sshd()
        folder = tmp_path / "sshd"
        folder.mkdir()
        self.port = free_port()
        keygen(folder / "host_key")
        keygen(folder / "client_key")
        authorized = folder / "authorized_keys"
        shutil.copy(folder / "client_key.pub", authorized)
        authorized.chmod(0o600)
        self.client_key = folder / "client_key"
        config = folder / "sshd_config"
        config.write_text("\n".join([
            f"Port {self.port}", "ListenAddress 127.0.0.1", f"HostKey {folder / 'host_key'}",
            f"PidFile {folder / 'sshd.pid'}", f"AuthorizedKeysFile {authorized}", "StrictModes no",
            "UsePAM no", "PasswordAuthentication no", "KbdInteractiveAuthentication no",
            "PubkeyAuthentication yes", "AuthenticationMethods publickey", ""]), "utf-8")
        self.log = folder / "sshd.log"
        with open(self.log, "wb") as log:
            self.process = subprocess.Popen([sshd, "-D", "-e", "-p", str(self.port), "-f", str(config)],
                                            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                            start_new_session=True)
        self.wait_listening()

    def wait_listening(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                pytest.fail(f"sshd exited at once ({self.process.returncode}): {self.log.read_text()}")
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return
            except OSError:
                time.sleep(0.1)
        pytest.fail(f"sshd is not listening: {self.log.read_text()}")

    def kill_group(self):
        """Kill sshd and its per-connection children. A connection's `sshd: user [priv]` and
        `sshd: user@notty` run in a session of their own, outside sshd's process group, so they are
        found through the parent chain; the command the connection started (the test machine's
        far side) is theirs too but is left alone: it must notice by itself that the connection
        is gone."""

        victims = [self.process.pid]
        table = processes()
        while grown := [pid for pid, parent, name in table
                        if name == "sshd" and parent in victims and pid not in victims]:
            victims += grown
        for pid in reversed(victims):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait()

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
    settings = TestSettings(machines=(machine,))
    places = {"own": OwnPlace(), PLACE: MachinePlace(machine, origin_url=str(repos.origin))}
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

    def kill_when_started():
        deadline = time.monotonic() + 120
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        sshd.kill_group()

    killer = threading.Thread(target=kill_when_started, daemon=True)
    killer.start()
    try:
        done = run_at(store, repos, settings, places)
        killer.join(timeout=30)
        yield store, done, int(pidfile.read_text())
    finally:  # never leave the sleeping test behind, whatever the verdict
        if pidfile.exists() and pidfile.read_text().strip() and alive(int(pidfile.read_text())):
            os.kill(int(pidfile.read_text()), signal.SIGKILL)


def test_the_sshd_killed_mid_run_is_lost_place_vanished_and_own_reruns_it(killed):
    store, done, _ = killed
    assert (done.place, done.outcome) == ("own", "passed")
    assert ended_runs(store, PLACE) == [("lost", "place_vanished", 0, 0)]


def test_the_sshd_killed_mid_run_writes_no_failed_run(killed):
    store, _, _ = killed
    assert store.query("SELECT COUNT(*) FROM events WHERE outcome = 'failed'").rows == [(0,)]


def test_the_test_process_on_the_machine_is_dead_within_15_seconds_of_the_sshd_dying(killed):
    _, _, pid = killed
    deadline = time.monotonic() + 15
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(pid)
