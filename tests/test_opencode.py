"""The opencode server lifecycle and its client (#396 decision 1), against the stand-in server in
`opencode_stub.py`: one server per run on its own port, a password nobody else knows, and a
process tree that is gone after the run, whatever ended it."""

import http.server
import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import pytest

from machine_repos import alive, dead_within
from orch_it.research import opencode
from orch_it.research.opencode import Client, OpencodeError, Server, find_opencode, free_port

STUB_PATH = str(Path(__file__).with_name("opencode_stub.py"))
STUB = [sys.executable, STUB_PATH]
CONFIG = {"provider": {"acme": {"options": {"apiKey": "{env:STUB_KEY}"}}}}
USER_MESSAGE = {"info": {"id": "m1", "role": "user"}, "parts": [{"type": "text", "text": "brief"}]}
ANSWER = {"info": {"id": "m2", "role": "assistant", "tokens": {"input": 5, "output": 2}},
          "parts": [{"type": "text", "text": "the report"}]}


class Stub:
    """One scenario file for the stub, and a server made from it."""

    def __init__(self, tmp_path, name="stub", **scenario):
        self.dir = tmp_path / name
        self.dir.mkdir()
        self.record = self.dir / "record.json"
        scenario.setdefault("mode", "finish")
        scenario["record"] = str(self.record)
        (self.dir / "scenario.json").write_text(json.dumps(scenario))
        self.env = {**os.environ, "STUB_KEY": "not-a-real-key",
                    "OPENCODE_STUB_SCENARIO": str(self.dir / "scenario.json")}

    def server(self, prefix=None, **kw):
        kw.setdefault("start_timeout", 20)
        work = self.dir / "work"
        work.mkdir(exist_ok=True)
        return Server(prefix or STUB, config=CONFIG, env=self.env, state_dir=self.dir / "state",
                      work_dir=work, **kw)

    def seen(self) -> dict:
        return json.loads(self.record.read_text())


class Squatter(http.server.ThreadingHTTPServer):
    """Another program holding a port: it listens and answers 404 to every call. Off the reuse flag,
    as the stub's listener is, so a bind to its port is refused on every OS."""

    allow_reuse_address = False


class SquatterHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def squatters():
    """A function that starts one squatter and returns it; every squatter is closed after the test."""
    started = []

    def squat():
        httpd = Squatter(("127.0.0.1", 0), SquatterHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return httpd

    yield squat
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def wait_idle(client, session, seconds=15):
    deadline = time.monotonic() + seconds
    while client.busy(session):
        assert time.monotonic() < deadline, "the session stayed busy"
        time.sleep(0.05)


def test_a_session_runs_from_prompt_to_idle_and_its_messages_are_read(tmp_path):
    stub = Stub(tmp_path, busy_seconds=3, root="root1",
                sessions={"root1": {"messages": [USER_MESSAGE, ANSWER], "children": ["kid1", "kid2"]},
                          "kid1": {"messages": [], "children": []}})
    with stub.server() as client:
        session = client.create_session("a title")
        assert session == "root1"
        client.prompt(session, agent="build", provider_id="acme", model_id="big-1", text="go")
        assert client.busy(session)
        wait_idle(client, session)
        assert client.messages(session) == [USER_MESSAGE, ANSWER]
        assert client.children(session) == ["kid1", "kid2"]
        assert client.children("kid1") == []
        client.abort(session)
    seen = stub.seen()
    assert seen["prompts"] == [{"agent": "build", "model": {"providerID": "acme", "modelID": "big-1"},
                                "system": "", "parts": [{"type": "text", "text": "go"}]}]
    assert seen["aborted"] == ["root1"]
    assert seen["exa"] == "1"
    assert seen["config"] == CONFIG
    assert (seen["key_env"], seen["key_present"]) == ("STUB_KEY", True)
    assert set(seen["directories"]) == {str(tmp_path / "stub" / "work")}


def test_a_wrong_password_is_an_http_error_401(tmp_path):
    stub = Stub(tmp_path)
    with stub.server() as client:
        wrong = Client(client.base_url, "not-the-password", client.directory)
        with pytest.raises(OpencodeError) as caught:
            wrong.busy("any")
    assert caught.value.code == "http_error"
    assert "GET /session/status: HTTP 401" in str(caught.value)


def test_a_server_that_exits_at_start_is_a_start_failure_and_nothing_is_left(tmp_path):
    stub = Stub(tmp_path, mode="exit")
    server = stub.server()
    with pytest.raises(OpencodeError) as caught:
        server.__enter__()
    assert caught.value.code == "server_start_failed"
    assert "server.log" in str(caught.value)
    assert dead_within(server.pid)


def test_a_program_that_cannot_start_is_a_start_failure(tmp_path):
    with pytest.raises(OpencodeError) as caught:
        Stub(tmp_path).server(["no-such-program-orch-it"]).__enter__()
    assert caught.value.code == "server_start_failed"


def test_a_port_another_program_holds_is_skipped_and_the_next_one_is_used(tmp_path, monkeypatch, squatters):
    taken = squatters().server_address[1]
    real_free_port = opencode.free_port
    picks = [taken]
    monkeypatch.setattr(opencode, "free_port", lambda: picks.pop(0) if picks else real_free_port())
    stub = Stub(tmp_path)
    server = stub.server()
    with server as client:
        assert client.busy("any") is False
        assert server.port != taken
    log = (stub.dir / "state" / "server.log").read_text()
    assert f"start on port {taken}" in log and f"start on port {server.port}" in log


def test_when_every_port_is_taken_the_start_fails_after_its_tries(tmp_path, monkeypatch, squatters):
    monkeypatch.setattr(opencode, "START_TRIES", 3)
    taken = [squatters().server_address[1] for _ in range(3)]
    ports = iter(taken)
    monkeypatch.setattr(opencode, "free_port", lambda: next(ports))
    stub = Stub(tmp_path)
    server = stub.server()
    with pytest.raises(OpencodeError) as caught:
        server.__enter__()
    assert caught.value.code == "server_start_failed"
    assert "no free port in 3 tries" in str(caught.value)
    assert "the last 1" in str(caught.value)
    log = (stub.dir / "state" / "server.log").read_text()
    assert log.count("start on port") == 3
    assert all(f"start on port {port}" in log for port in taken)
    assert server.port == taken[-1]
    assert dead_within(server.pid)


def test_three_servers_started_at_once_each_answer_on_a_port_of_their_own(tmp_path):
    for round_no in range(5):
        stubs = [Stub(tmp_path, f"round{round_no}-{i}", root=f"s{i}") for i in range(3)]
        servers = [stub.server() for stub in stubs]
        answers = [None] * 3
        errors = []
        alive_together = threading.Barrier(3)

        def run(i):
            try:
                with servers[i] as client:
                    answers[i] = (servers[i].port, client.busy("any") is False)
                    alive_together.wait(60)  # all three are up before any of them stops
            except Exception as e:  # reported below, from the test's own thread
                errors.append(repr(e))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        assert not any(thread.is_alive() for thread in threads), f"round {round_no}: a start hung"
        assert errors == [], f"round {round_no}"
        assert all(answer is not None and answer[1] for answer in answers), f"round {round_no}"
        assert len({answer[0] for answer in answers}) == 3, f"round {round_no}: ports {answers}"


def test_a_server_that_never_answers_is_cut_at_its_bound_and_its_tree_is_killed(tmp_path):
    server = Stub(tmp_path).server([sys.executable, "-c", "import time; time.sleep(600)"],
                                   start_timeout=1.5)
    started = time.monotonic()
    with pytest.raises(OpencodeError) as caught:
        server.__enter__()
    assert caught.value.code == "server_no_answer"
    assert time.monotonic() - started < 15
    assert dead_within(server.pid)


def test_the_stub_and_its_grandchild_are_gone_after_the_run(tmp_path):
    stub = Stub(tmp_path, spawn_grandchild=True)
    with stub.server():
        seen = stub.seen()
        assert alive(seen["pid"]) and alive(seen["grandchild_pid"])
    assert dead_within(seen["pid"]) and dead_within(seen["grandchild_pid"])


def test_the_tree_is_killed_when_the_run_raises(tmp_path):
    stub = Stub(tmp_path, spawn_grandchild=True)
    with pytest.raises(KeyboardInterrupt):
        with stub.server():
            seen = stub.seen()
            raise KeyboardInterrupt
    assert dead_within(seen["pid"]) and dead_within(seen["grandchild_pid"])


def test_two_servers_at_once_have_their_own_port_and_process(tmp_path):
    one, two = Stub(tmp_path, "one", root="a"), Stub(tmp_path, "two", root="b")
    with one.server() as first, two.server() as second:
        assert first.base_url != second.base_url
        assert first.create_session("x") == "a" and second.create_session("x") == "b"
        assert one.seen()["pid"] != two.seen()["pid"]


def test_the_log_names_the_port_and_never_holds_the_password(tmp_path):
    stub = Stub(tmp_path)
    server = stub.server()
    with server as client:
        client.busy("any")
    log = (stub.dir / "state" / "server.log").read_text()
    assert f"port {server.port}" in log
    assert client.password not in log
    assert "not-a-real-key" not in log


def test_a_reply_that_is_not_json_is_a_bad_reply():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html>not json</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        client = Client(f"http://127.0.0.1:{httpd.server_address[1]}", "pw", "/d")
        with pytest.raises(OpencodeError) as caught:
            client.busy("any")
    finally:
        httpd.shutdown()
    assert caught.value.code == "bad_reply"


def test_a_server_nobody_listens_on_is_unreachable():
    # the port stays bound and is never listened on: connections are refused, and no other run can take it
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        client = Client(f"http://127.0.0.1:{held.getsockname()[1]}", "pw", "/d")
        with pytest.raises(OpencodeError) as caught:
            client.messages("any")
    assert caught.value.code == "unreachable"


def test_a_call_that_takes_too_long_is_unreachable():
    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(2)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        client = Client(f"http://127.0.0.1:{httpd.server_address[1]}", "pw", "/d", timeout=0.3)
        started = time.monotonic()
        with pytest.raises(OpencodeError) as caught:
            client.messages("any")
        assert time.monotonic() - started < 1.5
    finally:
        httpd.shutdown()
    assert caught.value.code == "unreachable"


def test_a_free_port_can_be_listened_on_at_once():
    port = free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), http.server.BaseHTTPRequestHandler)
    httpd.server_close()


def test_the_program_comes_from_the_path_a_name_a_path_or_an_argv_prefix(tmp_path, monkeypatch):
    program = tmp_path / ("opencode.exe" if sys.platform == "win32" else "opencode")
    program.write_bytes(b"")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert find_opencode(None) == [shutil.which("opencode")]
    assert find_opencode("opencode") == [shutil.which("opencode")]
    assert find_opencode(str(program)) == [str(program)]
    assert find_opencode([sys.executable, STUB_PATH]) == [sys.executable, STUB_PATH]
    assert find_opencode((sys.executable, STUB_PATH)) == [sys.executable, STUB_PATH]


@pytest.mark.parametrize("setting", [None, "no-such-program-orch-it", [], [""], [3], (), 7,
                                     ["no-such-program-orch-it", "serve"],
                                     ("no-such-program-orch-it",)])
def test_a_program_that_is_not_there_is_opencode_missing(setting, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(OpencodeError) as caught:
        find_opencode(setting)
    assert caught.value.code == "opencode_missing"


def test_the_npm_shim_gives_way_to_the_real_binary_beside_it(tmp_path):
    real = tmp_path / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"")
    for name in ("opencode.cmd", "opencode.BAT", "opencode.ps1"):
        assert opencode.real_program(str(tmp_path / name), windows=True) == str(real)
    assert opencode.real_program(str(tmp_path / "opencode.cmd"), windows=False) == str(tmp_path / "opencode.cmd")
    other = tmp_path / "other"
    other.mkdir()
    assert opencode.real_program(str(other / "opencode.cmd"), windows=True) == str(other / "opencode.cmd")
    assert opencode.real_program(str(tmp_path / "opencode.exe"), windows=True) == str(tmp_path / "opencode.exe")


def test_an_unknown_error_code_is_refused():
    with pytest.raises(ValueError):
        OpencodeError("nope")
    assert OpencodeError("http_error", "x").code == "http_error"


class Canned:
    """A local HTTP server that answers every request with `body` and notes each request path."""

    def __init__(self, body: bytes = b"[]", on_request=None):
        self.paths = []
        canned = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def handle_any(self):
                canned.paths.append(self.requestline.split()[1])
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if on_request:
                    on_request()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = handle_any

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_a_port_that_another_program_answered_on_is_not_ours(tmp_path, monkeypatch):
    flag = tmp_path / "answered"

    def answer_once_the_program_is_gone():
        # the squatter answers the first poll only after the program started for the port has ended,
        # so the start sees the end before its own check (no race with the program's exit time)
        flag.write_text("x")
        deadline = time.monotonic() + 10
        while server.tree.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)

    with Canned(b"{}", on_request=answer_once_the_program_is_gone) as squatter:
        monkeypatch.setattr(opencode, "free_port", lambda: squatter.port)
        waits = ("import os, sys, time\n"
                 "while not os.path.exists(sys.argv[1]): time.sleep(0.01)\n")
        server = Stub(tmp_path).server([sys.executable, "-c", waits, str(flag), "--"])
        with pytest.raises(OpencodeError) as caught:
            server.__enter__()
    assert caught.value.code == "server_start_failed"
    assert dead_within(server.pid)


def test_session_ids_are_quoted_in_every_path():
    ids = ["a/b", "x?y=1", "a#b", "sp ace"]
    with Canned() as canned:
        client = Client(canned.url, "pw", "/d")
        for sid in ids:
            client.prompt(sid, agent="a", provider_id="p", model_id="m", text="t")
            client.messages(sid)
            client.children(sid)
            client.abort(sid)
    expected = [f"/session/{quote(sid, safe='')}/{tail}?directory=%2Fd"
                for sid in ids for tail in ("prompt_async", "message", "children", "abort")]
    assert canned.paths == expected


def test_a_reply_nested_too_deep_for_json_is_a_bad_reply():
    with Canned(b"[" * 200000 + b"]" * 200000) as canned:
        with pytest.raises(OpencodeError) as caught:
            Client(canned.url, "pw", "/d").messages("any")
    assert caught.value.code == "bad_reply"


def test_a_reply_past_the_size_cap_is_a_bad_reply(monkeypatch):
    monkeypatch.setattr(opencode, "MAX_REPLY_BYTES", 1000)
    with Canned(b'["' + b"x" * 1500 + b'"]') as canned:
        with pytest.raises(OpencodeError) as caught:
            Client(canned.url, "pw", "/d").messages("any")
    assert caught.value.code == "bad_reply"
    with Canned(b'["' + b"x" * 500 + b'"]') as canned:
        assert len(Client(canned.url, "pw", "/d").messages("any")) == 1


def test_a_state_folder_or_log_that_cannot_be_opened_starts_nothing(tmp_path):
    stub = Stub(tmp_path)
    (tmp_path / "afile").write_text("x")
    in_the_way = Server(STUB, config={}, env=stub.env, state_dir=tmp_path / "afile", work_dir=stub.dir)
    (stub.dir / "state2" / "server.log").mkdir(parents=True)
    log_is_a_folder = Server(STUB, config={}, env=stub.env, state_dir=stub.dir / "state2",
                             work_dir=stub.dir)
    for server in (in_the_way, log_is_a_folder):
        with pytest.raises(OpencodeError) as caught:
            server.__enter__()
        assert caught.value.code == "server_start_failed"
        assert server.pid is None and not stub.record.exists()


def test_children_keeps_only_ids_that_are_non_empty_strings():
    body = json.dumps([{"id": "ok"}, {"id": ""}, {"id": 5}, {"id": None}, {"title": "x"}, "str",
                       {"id": "also"}]).encode()
    with Canned(body) as canned:
        assert Client(canned.url, "pw", "/d").children("s") == ["ok", "also"]


def test_a_status_that_is_not_a_mapping_is_a_bad_reply():
    with Canned(b"[1]") as canned:
        with pytest.raises(OpencodeError) as caught:
            Client(canned.url, "pw", "/d").busy("s")
    assert caught.value.code == "bad_reply"
