"""The opencode server of one research run and the client that talks to it (#396 decision 1).

`Server` starts `opencode serve` on a free local port with a password made for this run, waits
until it answers, and on exit kills the whole process tree, whatever ended the run (process start
and stop go through `ProcessTree`, one helper per OS). `Client` is the few calls the bridge makes,
every one bounded and every failure a coded `OpencodeError`. `find_opencode` turns the setting
`research.opencode` (or the `PATH`) into the argv prefix that starts opencode; no path of any one
machine is in this file.
"""

from __future__ import annotations

import base64
import http.client
import json
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from orch_it.proctree import ProcessTree

OPENCODE_ERROR_CODES = ("opencode_missing", "server_start_failed", "server_no_answer", "http_error",
                        "unreachable", "bad_reply")
POLL_SECONDS = 0.25
CALL_SECONDS = 60.0
STATUS_SECONDS = 10.0
ANSWER_SECONDS = 3.0  # one try while the server starts
SHIM_SUFFIXES = (".cmd", ".bat", ".ps1")


class OpencodeError(Exception):
    """opencode could not be found, started or asked. `code` is one of `OPENCODE_ERROR_CODES`."""

    def __init__(self, code: str, detail: str = ""):
        if code not in OPENCODE_ERROR_CODES:
            raise ValueError(f"not an opencode error code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def find_opencode(setting) -> list[str]:
    """The argv prefix that starts opencode. `setting` is None (look on the `PATH`), a program
    name or path, or a non-empty list of strings (an argv prefix, whose first item is the
    program)."""

    if setting is None:
        prefix = ["opencode"]
    elif isinstance(setting, str) and setting:
        prefix = [setting]
    elif isinstance(setting, list) and setting and all(isinstance(item, str) for item in setting):
        prefix = list(setting)
    else:
        raise OpencodeError("opencode_missing", f"the setting is not a program or an argv prefix: {setting!r}")
    found = shutil.which(prefix[0]) if prefix[0] else None
    if found is None:
        raise OpencodeError("opencode_missing", f"{prefix[0] or '(empty)'} is not a program on this machine")
    return [real_program(found), *prefix[1:]]


def real_program(found: str, *, windows: bool | None = None) -> str:
    """On Windows the npm shim (`.cmd`, `.bat`, `.ps1`) starts opencode as a child process, which
    a stop by process id would miss: the real `opencode.exe` the package keeps beside the shim is
    used when it is there. Elsewhere, and for any other program, `found` itself."""

    if windows is None:
        windows = sys.platform == "win32"
    path = Path(found)
    if windows and path.suffix.lower() in SHIM_SUFFIXES:
        real = path.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
        if real.is_file():
            return str(real)
    return found


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    """The calls the bridge makes to one opencode server, as one directory (its working tree)."""

    def __init__(self, base_url: str, password: str, directory, *, timeout: float = CALL_SECONDS):
        self.base_url = base_url
        self.password = password
        self.directory = str(directory)
        self.timeout = timeout
        self.auth = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # it is local

    def call(self, method: str, path: str, body=None, *, timeout: float | None = None):
        url = f"{self.base_url}{path}?directory={urllib.parse.quote(self.directory, safe='')}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", self.auth)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            raise OpencodeError("http_error", f"{method} {path}: HTTP {e.code} "
                                f"{e.read().decode('utf-8', 'replace')[:300]}") from None
        except (OSError, http.client.HTTPException) as e:  # refused, reset, timed out, cut short
            raise OpencodeError("unreachable", f"{method} {path}: {e}") from None
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise OpencodeError("bad_reply", f"{method} {path}: not JSON: "
                                f"{raw.decode('utf-8', 'replace')[:300]}") from None

    def create_session(self, title: str) -> str:
        reply = self.call("POST", "/session", {"title": title})
        if not isinstance(reply, dict) or not isinstance(reply.get("id"), str):
            raise OpencodeError("bad_reply", f"POST /session: no session id in {reply!r:.300}")
        return reply["id"]

    def prompt(self, session_id: str, *, agent: str, provider_id: str, model_id: str, text: str) -> None:
        self.call("POST", f"/session/{session_id}/prompt_async",
                  {"agent": agent, "model": {"providerID": provider_id, "modelID": model_id},
                   "system": "", "parts": [{"type": "text", "text": text}]})

    def statuses(self) -> dict:
        reply = self.call("GET", "/session/status", timeout=STATUS_SECONDS)
        if reply is not None and not isinstance(reply, dict):
            raise OpencodeError("bad_reply", f"GET /session/status: not a mapping: {reply!r:.300}")
        return reply or {}

    def busy(self, session_id: str) -> bool:
        """Whether the session is working; one the server does not list is idle."""
        state = self.statuses().get(session_id)
        return isinstance(state, dict) and state.get("type", "idle") != "idle"

    def messages(self, session_id: str) -> list:
        reply = self.call("GET", f"/session/{session_id}/message")
        if reply is not None and not isinstance(reply, list):
            raise OpencodeError("bad_reply", f"GET /session/{session_id}/message: not a list")
        return reply or []

    def children(self, session_id: str) -> list[str]:
        reply = self.call("GET", f"/session/{session_id}/children")
        if reply is not None and not isinstance(reply, list):
            raise OpencodeError("bad_reply", f"GET /session/{session_id}/children: not a list")
        return [child["id"] for child in reply or [] if isinstance(child, dict) and "id" in child]

    def abort(self, session_id: str) -> None:
        self.call("POST", f"/session/{session_id}/abort")


class Server:
    """`with Server(...) as client:` runs one opencode server for the length of the block."""

    def __init__(self, argv_prefix: list[str], *, config: dict, env: dict[str, str], state_dir: Path,
                 work_dir: Path, start_timeout: float = 30):
        self.argv_prefix = list(argv_prefix)
        self.config = config
        self.env = env
        self.state_dir = Path(state_dir)
        self.work_dir = Path(work_dir)
        self.start_timeout = start_timeout
        self.tree: ProcessTree | None = None
        self.log = None
        self.port: int | None = None
        self.pid: int | None = None

    @property
    def log_path(self) -> Path:
        return self.state_dir / "server.log"

    def __enter__(self) -> Client:
        try:
            return self.start()
        except BaseException:  # nothing started here outlives the failure
            self.__exit__(None, None, None)
            raise

    def start(self) -> Client:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        password = secrets.token_urlsafe(24)
        env = {**self.env, "OPENCODE_SERVER_PASSWORD": password,
               "OPENCODE_CONFIG_CONTENT": json.dumps(self.config), "OPENCODE_ENABLE_EXA": "1",
               "OPENCODE_DISABLE_CLAUDE_CODE": "1"}
        self.log = open(self.log_path, "ab")
        self.log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} start on port {self.port}\n".encode())
        self.log.flush()
        argv = [*self.argv_prefix, "serve", "--hostname", "127.0.0.1", "--port", str(self.port)]
        try:
            self.tree = ProcessTree.start(argv, cwd=self.work_dir, env=env, stdout=self.log,
                                          stderr=subprocess.STDOUT)
        except (OSError, ValueError) as e:
            raise OpencodeError("server_start_failed", f"{argv[0]}: {e}") from None
        self.pid = self.tree.process.pid
        client = Client(f"http://127.0.0.1:{self.port}", password, self.work_dir)
        deadline = time.monotonic() + self.start_timeout
        last = ""
        while True:
            if self.tree.process.poll() is not None:
                raise OpencodeError("server_start_failed", f"opencode exited with code "
                                    f"{self.tree.process.returncode}, see {self.log_path}")
            try:
                client.call("GET", "/session/status", timeout=ANSWER_SECONDS)
                return client
            except OpencodeError as e:
                last = str(e)
            if time.monotonic() >= deadline:
                raise OpencodeError("server_no_answer", f"no answer in {self.start_timeout:g} s "
                                    f"(last: {last}), see {self.log_path}")
            time.sleep(POLL_SECONDS)

    def __exit__(self, *exc) -> bool:
        try:
            if self.tree is not None:
                self.tree.kill()
        finally:
            if self.log is not None:
                self.log.close()
                self.log = None
        return False
