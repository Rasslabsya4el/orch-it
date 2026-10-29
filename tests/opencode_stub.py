"""A stand-in for the `opencode` binary, for the tests of the research bridge (#396).

Run as `python opencode_stub.py serve --hostname 127.0.0.1 --port N`. It wants the Basic auth
password from `OPENCODE_SERVER_PASSWORD` and serves the few endpoints the bridge calls. What it
does comes from the JSON scenario file named by `OPENCODE_STUB_SCENARIO`:

    mode              finish | hang | stall | exit
                      finish: busy for `busy_seconds` after the prompt, then idle;
                      hang: busy forever and a new assistant message every 0.2 s (a run that
                      keeps working: only a time bound stops it);
                      stall: busy forever, one assistant message, then nothing new;
                      exit: the process ends at once with code 3 (a start failure)
    busy_seconds      finish only
    sessions          {id: {"messages": [...], "children": [id, ...]}}
    root              the id POST /session returns
    spawn_grandchild  start a long-sleeping child process at start
    record            a JSON file rewritten (atomically) with what the stub saw: pid,
                      grandchild_pid, prompts, config, key_env, key_present (never the value),
                      exa, aborted, directories

Standard library only; it holds no real path, host, user name or key.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from base64 import b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SCENARIO = {}
LOCK = threading.Lock()
STATE = {"prompted_at": None, "aborted": [], "prompts": [], "directories": []}
RECORD = {}


def write_record() -> None:
    path = SCENARIO.get("record")
    if not path:
        return
    temp = f"{path}.{os.getpid()}.tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(RECORD, f)
    for attempt in range(20):  # a reader may hold the file for a moment on Windows
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            time.sleep(0.05)


def key_facts(config: dict) -> tuple:
    """The environment variable the provider's `apiKey` names (`{env:NAME}`) and whether it is
    set and not empty here."""
    for provider in (config.get("provider") or {}).values():
        match = re.fullmatch(r"\{env:(\w+)\}", str((provider.get("options") or {}).get("apiKey", "")))
        if match:
            return match.group(1), bool(os.environ.get(match.group(1)))
    return None, False


def assistant_message(index: int, created_ms: int, text: str) -> dict:
    return {"info": {"id": f"gen{index}", "role": "assistant", "sessionID": SCENARIO.get("root", "stub"),
                     "time": {"created": created_ms}},
            "parts": [{"type": "step-start"}, {"type": "text", "text": text}]}


def root_id() -> str:
    return SCENARIO.get("root", "ses_stub")


def is_busy(session: str) -> bool:
    with LOCK:
        prompted = STATE["prompted_at"]
        if session != root_id() or prompted is None or session in STATE["aborted"]:
            return False
    if SCENARIO.get("mode") == "finish":
        return time.time() < prompted + float(SCENARIO.get("busy_seconds", 0.5))
    return True


def messages_of(session: str) -> list:
    listed = list((SCENARIO.get("sessions", {}).get(session) or {}).get("messages", []))
    with LOCK:
        prompted = STATE["prompted_at"]
        if session == root_id() and prompted is None:
            return listed[:1] if listed and listed[0].get("info", {}).get("role") == "user" else []
        if session == root_id() and SCENARIO.get("mode") in ("hang", "stall"):
            return listed + list(STATE.setdefault("generated", []))
    return listed


def generate_forever() -> None:
    index = 0
    while True:
        time.sleep(0.2)
        with LOCK:
            if STATE["prompted_at"] is None or root_id() in STATE["aborted"]:
                continue
            index += 1
            STATE.setdefault("generated", []).append(
                assistant_message(index, int(time.time() * 1000), f"step {index} of the work"))


class Listener(ThreadingHTTPServer):
    # HTTPServer sets allow_reuse_address, which on Windows lets this socket bind a port another
    # process already holds; off, a taken port refuses the bind, as on the real opencode
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status: int, body=None) -> None:
        with LOCK:
            write_record()  # before the answer: a caller that stops the stub then finds it current
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        try:
            user, _, password = b64decode(header.removeprefix("Basic ")).decode().partition(":")
        except ValueError:
            return False
        return user == "opencode" and password == os.environ.get("OPENCODE_SERVER_PASSWORD")

    def route(self, method: str) -> None:
        if not self.authorized():
            self.reply(401, {"error": "unauthorized"})
            return
        url = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        directory = parse_qs(url.query).get("directory", [None])[0]
        with LOCK:
            if directory is not None and directory not in STATE["directories"]:
                STATE["directories"].append(directory)
            RECORD["directories"] = list(STATE["directories"])
        parts = url.path.strip("/").split("/")
        if (method, parts) == ("GET", ["session", "status"]):
            self.reply(200, {root_id(): {"type": "busy"}} if is_busy(root_id()) else {})
        elif (method, parts) == ("POST", ["session"]):
            self.reply(200, {"id": root_id(), "title": (body or {}).get("title", "")})
        elif len(parts) == 3 and parts[0] == "session":
            self.session_call(method, parts[1], parts[2], body)
        else:
            self.reply(404, {"error": "not found"})

    def session_call(self, method: str, session: str, action: str, body) -> None:
        if (method, action) == ("POST", "prompt_async"):
            with LOCK:
                STATE["prompted_at"] = time.time()
                STATE["prompts"].append(body)
                RECORD["prompts"] = list(STATE["prompts"])
                if SCENARIO.get("mode") == "stall":
                    STATE["generated"] = [assistant_message(
                        1, int(STATE["prompted_at"] * 1000), "some text, and then nothing new")]
            self.reply(204)
        elif (method, action) == ("GET", "message"):
            self.reply(200, messages_of(session))
        elif (method, action) == ("GET", "children"):
            ids = (SCENARIO.get("sessions", {}).get(session) or {}).get("children", [])
            self.reply(200, [{"id": child} for child in ids])
        elif (method, action) == ("POST", "abort"):
            with LOCK:
                STATE["aborted"].append(session)
                RECORD["aborted"] = list(STATE["aborted"])
            self.reply(200, True)
        else:
            self.reply(404, {"error": "not found"})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")


def main(argv: list) -> int:
    if argv[:1] != ["serve"]:
        return 2
    options = dict(zip(argv[1::2], argv[2::2]))
    scenario_path = os.environ.get("OPENCODE_STUB_SCENARIO")
    if scenario_path:
        with open(scenario_path, encoding="utf-8") as f:
            SCENARIO.update(json.load(f))
    if SCENARIO.get("mode") == "exit":
        print("stub: told to exit at start", file=sys.stderr)
        return 3
    try:  # a taken port ends the process at once with code 1, as the real opencode does
        server = Listener((options.get("--hostname", "127.0.0.1"), int(options["--port"])), Handler)
    except OSError as e:
        print(f"stub: cannot listen on {options.get('--port')}: {e}", file=sys.stderr)
        return 1
    config = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
    key_env, key_present = key_facts(config)
    RECORD.update({"pid": os.getpid(), "prompts": [], "aborted": [], "directories": [], "config": config,
                   "key_env": key_env, "key_present": key_present,
                   "exa": os.environ.get("OPENCODE_ENABLE_EXA")})
    if SCENARIO.get("spawn_grandchild"):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                      creationflags=flags)
        RECORD["grandchild_pid"] = grandchild.pid
    write_record()
    if SCENARIO.get("mode") == "hang":
        threading.Thread(target=generate_forever, daemon=True).start()
    server.daemon_threads = True
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
