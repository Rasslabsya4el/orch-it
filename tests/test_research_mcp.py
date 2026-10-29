"""The MCP front of the research bridge (#396 decisions 1 and 5, rows 3, 5 and 6): the server
`python -m orch_it research-mcp` that `.mcp.json` registers as `orch-it-research`, started here
as Claude Code starts it (the environment `.mcp.json`'s `env` expands, nothing else of ours),
speaking newline-delimited JSON-RPC on stdin and stdout."""

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from orch_it import cli
from research_bridge import BRIEF, KEY_ENV, PROMPT_ONLY, Bridge, report_messages

REPO = Path(__file__).parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ENTRY = json.loads((REPO / ".mcp.json").read_text("utf-8"))["mcpServers"]["orch-it-research"]
# the provider key's variable `.mcp.json` hands the server (the settings' `research.key_env` names it)
KEY_ENV_IN_MCP = next(name for name in ENTRY["env"] if not name.startswith("ORCH_IT_"))


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def fill(text: str, base: dict) -> str:
    """`text` with each `${VAR}` / `${VAR:-default}` filled from `base`, as Claude Code fills `.mcp.json`."""

    def one(match):
        name, _, default = match.group(1).partition(":-")
        return base.get(name) or default
    return re.sub(r"\$\{([^}]+)\}", one, text)


def expanded_env(base: dict) -> dict:
    """The server's environment as Claude Code makes it from `.mcp.json`: its `env` with each
    `${VAR}` / `${VAR:-default}` filled from `base`, over the few variables any process needs."""
    keep = {name: value for name, value in base.items()
            if name.upper() in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
                                "LOCALAPPDATA", "APPDATA", "OPENCODE_STUB_SCENARIO", "PYTHONPATH")}
    return keep | {name: fill(value, base) for name, value in ENTRY.get("env", {}).items()}


class McpServer:
    """The server process and a JSON-RPC conversation with it, every read bounded."""

    def __init__(self, env: dict, cwd: Path):
        # the entry as `.mcp.json` names it: command `${ORCH_IT_PYTHON}`, args `-m orch_it research-mcp`;
        # this interpreter is what ORCH_IT_PYTHON names, so it is the command the server really runs under
        assert ENTRY["command"] == "${ORCH_IT_PYTHON}" and ENTRY["args"] == ["-m", "orch_it", "research-mcp"]
        command = fill(ENTRY["command"], {"ORCH_IT_PYTHON": sys.executable})
        self.process = subprocess.Popen([command, *ENTRY["args"]],
                                        cwd=cwd, env=env | {"ORCH_IT_PYTHON": sys.executable},
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        creationflags=NO_WINDOW)
        self.lines: list[str] = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.next_id = 0

    def _read(self):
        for line in self.process.stdout:
            self.lines.append(line.decode("utf-8"))

    def call(self, method: str, params: dict | None = None, seconds: float = 30) -> dict:
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}
        self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for line in list(self.lines):
                reply = json.loads(line)
                if reply.get("id") == self.next_id:
                    return reply
            time.sleep(0.02)
        raise AssertionError(f"no reply to {method} in {seconds} s")

    def tool(self, name: str, arguments: dict, seconds: float = 30) -> dict:
        reply = self.call("tools/call", {"name": name, "arguments": arguments}, seconds)["result"]
        text = reply["content"][0]["text"]
        return {"error": text} if reply.get("isError") else json.loads(text)

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=15)


@pytest.fixture
def mcp(tmp_path):
    servers = []

    def start(bridge: Bridge, *, with_key=True) -> McpServer:
        base = dict(bridge.env) | {"ORCH_IT_SETTINGS": str(bridge.settings_file),
                                   "ORCH_IT_STORE": str(bridge.store.root)}
        base[KEY_ENV_IN_MCP] = base.pop(KEY_ENV) if with_key else ""
        if not with_key:
            base.pop(KEY_ENV_IN_MCP)
        server = McpServer(expanded_env(base), bridge.work_tree)
        servers.append(server)
        server.call("initialize", {"protocolVersion": "2024-11-05"})
        return server

    yield start
    for server in servers:
        server.close()


def bench(tmp_path, **scenario) -> Bridge:
    """The bench with the key under the name `.mcp.json` passes, the settings naming it."""

    made = Bridge(tmp_path, key_env=KEY_ENV_IN_MCP, **scenario)
    subprocess.run(["git", "init", "-q", str(made.work_tree)], check=True, creationflags=NO_WINDOW)
    subprocess.run(["git", "-C", str(made.work_tree), "remote", "add", "origin",
                    "https://github.com/owner/repo.git"], check=True, creationflags=NO_WINDOW)
    return made


def wait_done(server: McpServer, job: str, seconds: float = 90) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = server.tool("research_job", {"job": job, "wait_s": 2})
        if status["state"] != "running":
            return status
    raise AssertionError("the job did not end")


def test_the_tools_list_offers_the_aliases_of_the_settings(tmp_path, mcp):
    server = mcp(bench(tmp_path))
    tools = {tool["name"]: tool for tool in server.call("tools/list")["result"]["tools"]}
    assert set(tools) == {"research_start", "research_job"}
    assert tools["research_start"]["inputSchema"]["properties"]["model"]["enum"] == ["tiny", "other"]


def test_a_brief_through_the_mcp_returns_the_report_text_and_its_path(tmp_path, mcp):
    bridge = bench(tmp_path, messages=report_messages("lost-report-380"))
    server = mcp(bridge)
    started = server.tool("research_start", {"brief": BRIEF, "model": "tiny"})
    assert started["state"] == "running"
    done = wait_done(server, started["job"])
    target = bridge.work_tree / "docs" / "research" / "2026-10-26-test.md"
    assert (done["state"], done["outcome"], done["report_from"]) == ("ended", "passed", "earlier")
    assert done["report_path"] == str(target) and done["report"] == target.read_text("utf-8")
    assert len(done["report"]) == 38973
    assert bridge.seen()["prompts"][0]["model"]["modelID"] == "model-x-9"
    assert bridge.seen()["key_present"] is True


def test_the_cli_and_the_mcp_end_with_the_same_shape_and_the_same_events(tmp_path, mcp, monkeypatch):
    for name in ("mcp", "cli"):
        (tmp_path / name).mkdir()
    by_mcp = bench(tmp_path / "mcp")
    server = mcp(by_mcp)
    done = wait_done(server, server.tool("research_start", {"brief": BRIEF, "model": "tiny"})["job"])

    by_cli = bench(tmp_path / "cli")
    for name, value in by_cli.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(KEY_ENV_IN_MCP, "not-a-real-key")
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(by_cli.settings_file))
    (by_cli.tmp / "brief.md").write_text(BRIEF, "utf-8")
    shape_file = by_cli.tmp / "done.json"
    assert cli.main(["--store", str(by_cli.store.root), "research", str(by_cli.tmp / "brief.md"), "--model", "tiny",
                     "--work-tree", str(by_cli.work_tree), "--repo", "owner/repo",
                     "--done-file", str(shape_file)]) == 0
    shape = json.loads(shape_file.read_text("utf-8"))

    assert set(shape) <= set(done)
    varying = {"run", "minutes", "report_path", "partial_path", "opencode_session", "job"}
    assert {k: v for k, v in shape.items() if k not in varying} == {
        k: done[k] for k in shape if k not in varying}

    def events(bridge):
        out = []
        for kind, detail in bridge.store.query(
                "SELECT kind, detail FROM events WHERE step = 'research_run' AND kind != 'step.progressed'"
                " ORDER BY at").rows:
            detail = json.loads(detail)
            out.append((kind, {k: v for k, v in detail.items() if k not in varying and k != "brief"}))
        return out

    assert events(by_cli) == events(by_mcp)


def test_with_the_key_in_the_mcp_env_the_server_finds_it_and_without_it_refuses_naming_it(tmp_path, mcp):
    bridge = bench(tmp_path)
    refused = mcp(bridge, with_key=False).tool("research_start", {"brief": BRIEF})
    assert "key_missing" in refused["error"] and KEY_ENV_IN_MCP in refused["error"]
    assert not bridge.record.exists()


def test_an_alias_not_in_the_settings_is_refused_before_any_server(tmp_path, mcp):
    bridge = bench(tmp_path)
    refused = mcp(bridge).tool("research_start", {"brief": BRIEF, "model": "luna"})
    assert "unknown_model" in refused["error"] and "tiny" in refused["error"]
    assert not bridge.record.exists()


def test_a_bounded_job_ends_no_result_with_the_partial_report(tmp_path, mcp):
    bridge = bench(tmp_path, mode="hang", messages=PROMPT_ONLY, minutes=0.03)
    server = mcp(bridge)
    done = wait_done(server, server.tool("research_start", {"brief": BRIEF})["job"])
    assert (done["state"], done["outcome"], done["reason"]) == ("ended", "no_result", "time_bound")
    assert done["partial_path"].endswith("2026-10-26-test.partial.md") and "report" not in done
    assert "of the work" in done["partial"]
    bridge.assert_tree_gone()


@pytest.mark.parametrize("job", ["../x", "", "a/b", "nope-20260101"])
def test_an_unknown_or_bad_job_is_an_error_not_a_crash(tmp_path, mcp, job):
    server = mcp(bench(tmp_path))
    assert "unknown_job" in server.tool("research_job", {"job": job})["error"]
    assert server.call("ping")["result"] == {}


def test_a_line_that_is_no_json_gets_a_parse_error_and_unknown_methods_are_answered(tmp_path, mcp):
    server = mcp(bench(tmp_path))
    server.process.stdin.write(b"not json\n\n[1,2]\n")
    server.process.stdin.flush()
    assert server.call("no/such")["error"]["code"] == -32601
    assert any(json.loads(line).get("error", {}).get("code") == -32700 for line in server.lines)


@pytest.mark.parametrize("arguments", [
    {"brief": BRIEF, "model": ["tiny"]}, {"brief": BRIEF, "report_path": 5}, {"brief": BRIEF, "repo": 5},
    {"brief": BRIEF, "ticket": True}, {"brief": BRIEF, "ticket": -3}, {"brief": BRIEF, "no_reddit": "false"},
    {"brief": 7}, {"brief": "   "}, {},
], ids=["model-list", "path-number", "repo-number", "ticket-bool", "ticket-negative", "no-reddit-text",
        "brief-number", "brief-blank", "no-brief"])
def test_an_argument_of_the_wrong_type_is_refused_and_the_server_lives_on(tmp_path, mcp, arguments):
    bridge = bench(tmp_path)
    server = mcp(bridge)
    assert "bad_argument" in server.tool("research_start", arguments)["error"]
    assert server.call("ping")["result"] == {} and not bridge.record.exists()


def test_a_brief_that_is_no_text_is_refused(tmp_path, mcp):
    server = mcp(bench(tmp_path))
    line = '{"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {"name": "research_start", ' \
           '"arguments": {"brief": "\\ud800 brief"}}}\n'
    server.process.stdin.write(line.encode("utf-8"))
    server.process.stdin.flush()
    deadline = time.monotonic() + 30
    while not any(json.loads(text).get("id") == 99 for text in server.lines):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert server.call("ping")["result"] == {}


def test_a_wait_that_is_no_finite_number_does_not_hang_the_server(tmp_path, mcp):
    bridge = bench(tmp_path, mode="hang", messages=PROMPT_ONLY, minutes=0.2)
    server = mcp(bridge)
    job = server.tool("research_start", {"brief": BRIEF})["job"]
    for wait in ("NaN", "Infinity", '"soon"', "-5", "true"):
        line = ('{"jsonrpc": "2.0", "id": %d, "method": "tools/call", "params": {"name": "research_job", '
                '"arguments": {"job": "%s", "wait_s": %s}}}\n' % (1000 + len(wait), job, wait))
        server.process.stdin.write(line.encode("utf-8"))
        server.process.stdin.flush()
    started = time.monotonic()
    assert server.call("ping", seconds=70)["result"] == {}
    assert time.monotonic() - started < 60
    wait_done(server, job)


def test_a_job_of_an_earlier_server_whose_run_went_silent_is_gone(tmp_path, mcp):
    bridge = bench(tmp_path)
    folder = tmp_path / "state" / "jobs" / "20260101-000000-abcdefgh"
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps({"started": time.time() - 3600}), "utf-8")
    status = mcp(bridge).tool("research_job", {"job": folder.name})
    assert (status["state"], status["reason"]) == ("gone", "worker_lost")
