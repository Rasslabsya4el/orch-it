"""The MCP front of the research bridge (#396 decisions 1 and 5): `python -m orch_it research-mcp`,
registered in orch-it's `.mcp.json` as `orch-it-research`, newline-delimited JSON-RPC on stdin and
stdout, standard library only.

Two tools. `research_start(brief, model?, no_reddit?, report_path?, ticket?, repo?)` refuses at
once what the settings cannot serve (the same refusals as the command line, the key's variable
named), then starts a job: the command-line front itself, `python -m orch_it research`, as a
background process with no console window and in its own process group, its brief, log and
answer in `<state>/jobs/<job>/`. A research run takes many minutes, longer than one MCP call may
last; the job outlives a restart of this server. `research_status(job, wait_s?)` answers
`running` (with the run's latest progress from the store), `done` with the run's whole result
shape plus the report text and its path, or `failed` when the job's process ended with no answer
or the job outlived its bounds. The provider key reaches this server only through `.mcp.json`'s
`env` expansion (`${VAR}`), never from the registry or a file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from orch_it import settings as installation
from orch_it.events.ids import live_id
from orch_it.events.store import Store, StoreError
from orch_it.research import run
from orch_it.research.opencode import OpencodeError, find_opencode
from orch_it.research.settings import ResearchRefused, ResearchSettings
from orch_it.settings import SettingsError

SERVER_NAME = "orch-it-research"
PROTOCOL = "2024-11-05"
MAX_WAIT_SECONDS = 50  # one call stays well under a client's request timeout
LOST_AFTER_SECONDS = 5 * 60  # past its time bound plus this, a job with no answer is lost
_JOB = re.compile(r"[0-9a-z][0-9a-z-]{7,80}")


class McpError(Exception):
    """A tool call that cannot be served. `code`: `unknown_job`, `bad_argument`, `unknown_tool`."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def tools(research: ResearchSettings) -> list[dict]:
    model = {"type": "string", "description": "a model alias of the settings; default by brief size"}
    if research.models:
        model["enum"] = list(research.models)
    return [
        {"name": "research_start",
         "description": ("Start a web research by a GPT model through opencode (it can open Reddit and forums). "
                         "Returns a job at once; the run takes minutes: then call research_status."),
         "inputSchema": {"type": "object", "required": ["brief"], "properties": {
             "brief": {"type": "string", "description": "the whole brief; a `docs/research/<name>.md` path in "
                                                        "backticks names where the report goes"},
             "model": model,
             "no_reddit": {"type": "boolean", "description": "primary sources: no Reddit link required"},
             "report_path": {"type": "string", "description": "write the report here (relative to the work tree)"},
             "ticket": {"type": "integer"},
             "repo": {"type": "string", "description": "owner/name; default the work tree's origin"}}}},
        {"name": "research_status",
         "description": "A job's state; once done, the run's result with the report text and its path.",
         "inputSchema": {"type": "object", "required": ["job"], "properties": {
             "job": {"type": "string"},
             "wait_s": {"type": "number", "description": f"wait up to this long for the end (max {MAX_WAIT_SECONDS})"}}}},
    ]


class Jobs:
    """The jobs of one work tree: started as background processes, answered from their folders."""

    def __init__(self, env: dict, work_tree: Path):
        self.env = env
        self.work_tree = work_tree
        self.started: dict[str, subprocess.Popen] = {}

    def settings(self) -> ResearchSettings:
        return ResearchSettings.from_settings(installation.load())

    def start(self, arguments: dict) -> dict:
        brief = arguments.get("brief")
        if not isinstance(brief, str) or not brief.strip():
            raise McpError("bad_argument", "brief")
        research = self.settings()
        alias = arguments.get("model") or research.alias_for(len(brief))
        research.model_id(alias)
        research.key(self.env)
        try:
            find_opencode(list(research.opencode) if isinstance(research.opencode, tuple) else research.opencode)
        except OpencodeError as e:
            raise ResearchRefused("opencode_missing", str(e)) from None
        target = run.report_path_of(brief, self.work_tree, arguments.get("report_path"))
        repo = arguments.get("repo") or run.repo_of(self.work_tree)

        job = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{live_id()[-8:].lower()}"
        folder = research.state / "jobs" / job
        folder.mkdir(parents=True)
        brief_file = folder / f"{target.stem if target else 'brief'}.md"
        brief_file.write_text(brief, "utf-8", newline="\n")
        argv = [sys.executable, "-m", "orch_it", "research", str(brief_file), "--work-tree", str(self.work_tree),
                "--repo", repo, "--model", alias, "--done-file", str(folder / "done.json")]
        if arguments.get("no_reddit"):
            argv.append("--no-reddit")
        if target is not None:
            argv += ["--report", str(target)]
        if isinstance(arguments.get("ticket"), int) and not isinstance(arguments.get("ticket"), bool):
            argv += ["--ticket", str(arguments["ticket"])]
        bound = (research.minutes + research.stall_minutes) * 60 + LOST_AFTER_SECONDS
        (folder / "job.json").write_text(json.dumps({"started": time.time(), "lost_after": bound,
                                                     "brief_sha": run.brief_sha(brief)}), "utf-8")
        with open(folder / "worker.log", "ab") as log:
            self.started[job] = subprocess.Popen(argv, cwd=self.work_tree, env=self.env, stdin=subprocess.DEVNULL,
                                                 stdout=log, stderr=subprocess.STDOUT, **_background())
        return {"job": job, "state": "running", "model": alias, "report_path": str(target) if target else None,
                "next": f"call research_status with this job (wait_s up to {MAX_WAIT_SECONDS}) until it is not running"}

    def status(self, arguments: dict) -> dict:
        job = arguments.get("job")
        if not isinstance(job, str) or not _JOB.fullmatch(job):
            raise McpError("unknown_job", str(job))
        folder = self.settings().state / "jobs" / job
        if not (folder / "job.json").is_file():
            raise McpError("unknown_job", job)
        wait = arguments.get("wait_s") or 0
        wait = min(max(float(wait), 0.0), MAX_WAIT_SECONDS) if isinstance(wait, (int, float)) else 0.0
        deadline = time.monotonic() + wait
        while True:
            done = _read_json(folder / "done.json")
            if done is not None:
                return self._done(job, done)
            ended = self._ended(job, folder)
            if ended:
                done = _read_json(folder / "done.json")  # it may have just answered
                return self._done(job, done) if done is not None else {
                    "job": job, "state": "failed", "reason": ended, "log_tail": _tail(folder / "worker.log")}
            if time.monotonic() >= deadline:
                return {"job": job, "state": "running", "progress": self._progress(folder)}
            time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))

    def _done(self, job: str, done: dict) -> dict:
        out = {"job": job, "state": "done", **done}
        if done.get("report_path"):
            try:
                out["report"] = Path(done["report_path"]).read_text("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                out["report_unreadable"] = str(e)
        return out

    def _ended(self, job: str, folder: Path) -> str | None:
        """Why a job with no answer is over: `worker_died` (its process ended), `worker_lost`
        (started by an earlier server and past its bounds), or None while it may still answer."""

        process = self.started.get(job)
        if process is not None:
            return "worker_died" if process.poll() is not None else None
        facts = _read_json(folder / "job.json") or {}
        started, lost_after = facts.get("started"), facts.get("lost_after")
        if isinstance(started, (int, float)) and isinstance(lost_after, (int, float)):
            return "worker_lost" if time.time() > started + lost_after else None
        return "worker_lost"

    def _progress(self, folder: Path) -> dict | None:
        """The latest `step.progressed` of the job's run, found by its brief's hash."""

        sha = (_read_json(folder / "job.json") or {}).get("brief_sha")
        try:
            store = Store.open()
            rows = store.query(
                "SELECT p.detail FROM events s JOIN events p ON p.run = s.run AND p.kind = 'step.progressed'"
                " WHERE s.kind = 'step.started' AND s.step = 'research_run' AND s.brief_sha = :sha"
                " ORDER BY s.at DESC, p.at DESC LIMIT 1", {"sha": sha}).rows
        except (StoreError, SettingsError):
            return None
        return json.loads(rows[0][0]) if rows else None


def handle(message, jobs: Jobs) -> dict | None:
    """The reply to one JSON-RPC message, or None for a notification or a line that is no request."""

    if not isinstance(message, dict):
        return None
    method, message_id = message.get("method"), message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "initialize":
        return _reply(message_id, {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                                   "capabilities": {"tools": {}},
                                   "serverInfo": {"name": SERVER_NAME, "version": "1"}})
    if method == "ping":
        return _reply(message_id, {})
    if method == "tools/list":
        try:
            research = jobs.settings()
        except SettingsError:
            research = ResearchSettings()
        return _reply(message_id, {"tools": tools(research)})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            if name == "research_start":
                out = jobs.start(arguments)
            elif name == "research_status":
                out = jobs.status(arguments)
            else:
                raise McpError("unknown_tool", str(name))
        except (McpError, ResearchRefused, SettingsError, OSError) as e:
            return _reply(message_id, {"isError": True, "content": [{"type": "text", "text": f"error: {e}"}]})
        return _reply(message_id, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}]})
    if message_id is None:
        return None
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin=None, stdout=None, *, env=None, work_tree: Path | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for stream in (stdin, stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    jobs = Jobs(dict(os.environ if env is None else env), Path(work_tree or Path.cwd()))
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = handle(message, jobs)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def _reply(message_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _background() -> dict:
    """Popen options for a job's process: no console window, its own process group, so neither a
    console's Ctrl+C nor this server's end takes it down."""

    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _read_json(path: Path) -> dict | None:
    try:
        found = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return found if isinstance(found, dict) else None


def _tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text("utf-8", "replace").splitlines()[-lines:])
    except OSError:
        return ""
