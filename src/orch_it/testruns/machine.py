"""A machine place (#394 decision 4): one run of the test command on another machine over ssh.

One `run` is one ssh connection to the machine. Its first stdin line is Python source that
unpacks a small bundle of this package's stdlib-only modules (see `remote`) and starts `remote.main`;
the second is the request. The far side checks the commit out in the machine's own folder, runs the
command and reports on stdout in `ORCH-IT` lines; the caller's work tree is never touched. Stdin is
kept open until the run ends: closing it is how the far side learns the caller is gone.

How a run ends: the report arrives, and `run` returns it; ssh cannot connect, exits before the
command started, the far setup fails (clone, fetch, unknown commit) or the setup passes the
bound: `PlaceRefused("place_off")` with the reason in the message; ssh exits or the connection
drops after the command started and before its report arrived, or the run passes its bound plus the
setup allowance: ssh is killed and `PlaceVanished`.
"""

from __future__ import annotations

import base64
import io
import json
import queue
import subprocess
import threading
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from orch_it.testruns.config import Machine
from orch_it.testruns.places import LOG_TAIL_CHARS, PlaceEnd, PlaceRefused, PlaceVanished, Request

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PREFIX = b"ORCH-IT "
FOLDER = Path(__file__).parent
BUNDLE = ("environment.py", "places.py", "process.py", "remote.py", "pytest_plugin/__init__.py",
          "pytest_plugin/orch_it_ids.py")
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=4")
REMOTE_PROGRAM = "import sys;exec(sys.stdin.readline())"
SETUP_SECONDS = 600.0  # allowed on top of the run's bound: connect, clone or fetch, checkout
STDERR_TAIL_BYTES = 2000
GRACE_SECONDS = 5.0  # ssh's time to go by itself once its stdin is closed, before it is killed


def bootstrap_line() -> bytes:
    """The first stdin line: Python source that unpacks the bundle into a temp folder, puts it
    first on `sys.path` and calls `remote.main` with that folder."""

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("orch_it/__init__.py", "")
        bundle.writestr("orch_it/testruns/__init__.py", "")
        for name in BUNDLE:
            bundle.writestr(f"orch_it/testruns/{name}", (FOLDER / name).read_bytes())
    packed = base64.b64encode(archive.getvalue()).decode("ascii")
    return (f"import base64,io,sys,tempfile,zipfile;d=tempfile.mkdtemp(prefix='orch-it-');"
            f"zipfile.ZipFile(io.BytesIO(base64.b64decode('{packed}'))).extractall(d);"
            f"sys.path.insert(0,d);from orch_it.testruns.remote import main;main(d)\n").encode("ascii")


@dataclass
class MachinePlace:
    machine: Machine
    origin_url: str | None = None  # the machine's own `origin` setting wins over it
    ssh: tuple[str, ...] = ("ssh",)
    setup_seconds: float = SETUP_SECONDS
    id: str = field(init=False)
    os: str = field(init=False)

    def __post_init__(self):
        self.id = self.machine.place
        self.os = self.machine.os

    def remote_command(self) -> str:
        """The one string the far shell gets. Double quotes around the program work in a POSIX
        shell and in Windows cmd.exe alike."""

        python = self.machine.python or ("python" if self.machine.os == "windows" else "python3")
        if any(c.isspace() for c in python):
            python = f'"{python}"'
        return f'{python} -c "{REMOTE_PROGRAM}"'

    def run(self, request: Request, scratch: Path) -> PlaceEnd:
        if not request.commit:
            raise PlaceRefused("place_off", "the work tree has no commit to check out on the machine")
        origin = self.machine.origin or self.origin_url
        if not origin:
            raise PlaceRefused("place_off", "no origin url to clone the machine's checkout from")
        argv = [*self.ssh, *SSH_OPTIONS, *self.machine.ssh_options, self.machine.host, self.remote_command()]
        started = time.monotonic()
        deadline = started + request.bound_seconds + self.setup_seconds
        try:
            ssh = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        except (OSError, ValueError) as e:
            raise PlaceRefused("place_off", f"ssh could not be started: {e}") from None
        lines: queue.Queue = queue.Queue()
        stderr: deque[bytes] = deque()
        threading.Thread(target=_read_lines, args=(ssh.stdout, lines), daemon=True).start()
        threading.Thread(target=_keep_tail, args=(ssh.stderr, stderr), daemon=True).start()
        sent = threading.Event()
        threading.Thread(target=_send, args=(ssh.stdin, [bootstrap_line(), _request_line(
            self.machine, origin, request)], sent), daemon=True).start()
        try:
            return self._read(lines, stderr, ssh, scratch, started, deadline)
        finally:
            self._close(ssh, sent)

    def _read(self, lines, stderr, ssh, scratch, started, deadline) -> PlaceEnd:
        start = end = None
        while True:
            try:
                line = lines.get(timeout=max(deadline - time.monotonic(), 0.0))
            except queue.Empty:
                if start is None:
                    raise PlaceRefused("place_off", f"{self.machine.host}: the run did not start in time"
                                       f"{_said(stderr)}") from None
                raise PlaceVanished(f"{self.machine.host}: no end of the run within its bound") from None
            if line is None:
                if start is None:
                    raise PlaceRefused("place_off", f"{self.machine.host}: ssh ended ({_exit_of(ssh)}) before "
                                       f"the run started{_said(stderr)}")
                raise PlaceVanished(f"{self.machine.host}: the connection ended mid-run{_said(stderr)}")
            kind, _, payload = line.partition(b" ")
            if kind == b"SETUP-FAILED":
                failed = _json(payload)
                raise PlaceRefused("place_off", f"{self.machine.host}: setup failed at "
                                   f"{failed.get('step')}: {failed.get('message')}")
            if kind == b"START":
                start = time.monotonic() - started
            elif kind == b"END" and start is not None:
                end = _json(payload)
            elif kind == b"REPORT" and end is not None:
                report = None
                if payload.strip():
                    report = scratch / "junit.xml"
                    report.write_bytes(base64.b64decode(payload))
                return PlaceEnd(report, bool(end.get("cut")), bool(end.get("slept")), end.get("rc"), start,
                                float(end.get("seconds") or 0.0), str(end.get("log_tail") or "")[-LOG_TAIL_CHARS:])

    @staticmethod
    def _close(ssh: subprocess.Popen, sent: threading.Event) -> None:
        """End the connection: closing stdin tells a live far side to kill its test tree; ssh is
        killed when it does not go by itself. A sender still blocked on a pipe nobody reads holds
        the pipe's lock: then ssh is killed at once."""

        if sent.is_set():
            try:
                ssh.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                ssh.wait(timeout=GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        if ssh.poll() is None:
            ssh.kill()
        ssh.wait()


def _json(payload: bytes) -> dict:
    try:
        value = json.loads(payload)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _exit_of(ssh: subprocess.Popen) -> int | None:
    try:
        return ssh.wait(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return None


def _said(stderr: deque[bytes]) -> str:
    time.sleep(0.2)  # let the stderr reader catch up with an ssh that has just ended
    text = b"".join(list(stderr))[-STDERR_TAIL_BYTES:].decode("utf-8", "replace").strip()
    return f": {text}" if text else ""


def _request_line(machine: Machine, origin: str, request: Request) -> bytes:
    return (json.dumps({"checkout": machine.checkout, "origin": origin,
                        "commit": request.commit, "command": list(request.command),
                        "test_env": request.test_env, "bound_seconds": request.bound_seconds,
                        "ids": request.ids}) + "\n").encode("utf-8")


def _send(pipe, chunks: list[bytes], sent: threading.Event) -> None:
    try:
        for chunk in chunks:
            pipe.write(chunk)
            pipe.flush()
    except (OSError, ValueError):
        pass
    sent.set()


def _read_lines(pipe, lines: queue.Queue) -> None:
    """The protocol lines of ssh's stdout, prefix cut; `None` at its end."""

    try:
        for raw in pipe:
            if raw.startswith(PREFIX):
                lines.put(raw[len(PREFIX):].rstrip(b"\r\n"))
    except (OSError, ValueError):
        pass
    lines.put(None)


def _keep_tail(pipe, kept: deque[bytes]) -> None:
    """Read ssh's stderr to its end, keeping about the last `STDERR_TAIL_BYTES`."""

    size = 0
    try:
        while chunk := pipe.read1(4096):
            kept.append(chunk)
            size += len(chunk)
            while size - len(kept[0]) >= STDERR_TAIL_BYTES:
                size -= len(kept.popleft())
    except (OSError, ValueError):
        pass
