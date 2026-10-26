"""The test bench of the research bridge (#396): a scenario for the opencode stand-in
(`opencode_stub.py`), the `research` settings that point at it (also written as a settings file
for the fronts), a store and a work tree."""

import json
import os
import sys
from pathlib import Path

from machine_repos import dead_within
from orch_it.events.store import Store
from orch_it.research import run
from orch_it.research.settings import ResearchSettings

FIXTURES = Path(__file__).parent / "fixtures" / "opencode"
STUB = [sys.executable, str(Path(__file__).with_name("opencode_stub.py"))]
KEY_ENV = "ORCH_IT_TEST_RESEARCH_KEY"
PROMPT_ONLY = [{"info": {"id": "m0", "role": "user"}, "parts": [{"type": "text", "text": "brief"}]}]
BRIEF = "# Brief: three questions\n\nReport file: `docs/research/2026-10-26-test.md`\n\n1. What?\n"


def report_messages(name: str) -> list:
    return json.loads((FIXTURES / "report" / f"{name}.json").read_text("utf-8"))["messages"]


class Bridge:
    """A stub scenario, the settings that point at it, a store and a work tree."""

    def __init__(self, tmp_path: Path, *, mode="finish", messages=None, tree=None, **research):
        self.tmp = tmp_path
        self.record = tmp_path / "record.json"
        sessions = {"run": {"messages": messages if messages is not None else report_messages("report-alone"),
                            "children": []}}
        if tree is not None:
            fixture = json.loads((FIXTURES / "tree" / f"{tree}.json").read_text("utf-8"))
            for sid, session in fixture["sessions"].items():
                if sid == "run":
                    sessions["run"]["children"] = session["children"]
                else:
                    sessions[sid] = session
        scenario = {"mode": mode, "busy_seconds": 0.3, "root": "run", "sessions": sessions,
                    "spawn_grandchild": True, "record": str(self.record)}
        (tmp_path / "scenario.json").write_text(json.dumps(scenario), "utf-8")
        self.env = {**os.environ, KEY_ENV: "not-a-real-key",
                    "OPENCODE_STUB_SCENARIO": str(tmp_path / "scenario.json")}
        self.raw = {"research": {
            "provider": {"id": "stub-provider", "options": {"baseURL": "http://127.0.0.1:9/v1"}},
            "key_env": KEY_ENV, "models": {"tiny": "model-x-9", "other": "model-y-1"},
            "opencode": STUB, "state": str(tmp_path / "state")} | research}
        self.settings = ResearchSettings.from_settings(self.raw)
        self.settings_file = tmp_path / "settings.json"
        self.settings_file.write_text(json.dumps(self.raw), "utf-8")
        self.store = Store.open(tmp_path / "events")
        self.work_tree = tmp_path / "work tree"
        self.work_tree.mkdir()

    def run(self, brief=BRIEF, **kw):
        kw.setdefault("poll_seconds", 0.05)
        kw.setdefault("progress_seconds", 0.2)
        kw.setdefault("grace_seconds", 5)
        kw.setdefault("env", self.env)
        return run.research(brief, settings=self.settings, store=self.store, repo="owner/repo",
                            work_tree=self.work_tree, brief_name="test-brief", **kw)

    def seen(self) -> dict:
        return json.loads(self.record.read_text("utf-8"))

    def events(self, kind=None) -> list[dict]:
        sql = "SELECT kind, detail FROM events WHERE step = 'research_run' ORDER BY at"
        rows = [(kind_, json.loads(detail)) for kind_, detail in self.store.query(sql).rows]
        return [detail for kind_, detail in rows if kind is None or kind_ == kind]

    def assert_tree_gone(self):
        seen = self.seen()
        assert dead_within(seen["pid"]) and dead_within(seen["grandchild_pid"])
