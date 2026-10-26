"""The brain's context line as a check (#399 decision 2): on each owner message, a UserPromptSubmit hook
reads the brain session's own transcript, and past the brain's context line (a setting of the roles
settings) adds a reminder to the turn and writes `context.crossed`. As a sentence, the context line was
skipped in 3 of 5 brain sessions (#380)."""

import json
from pathlib import Path

import pytest

from orch_it.events.store import Store
from orch_it.roles import RoleSet
from orch_it.roles.context import run_prompt_submit

LINE = RoleSet.load().roles["brain"].report.context_line


def transcript(tmp_path: Path, context: int) -> Path:
    path = tmp_path / "brain.jsonl"
    record = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
                                               "usage": {"input_tokens": 10, "cache_read_input_tokens": context}}}
    path.write_text(json.dumps(record) + "\n", "utf-8")
    return path


def payload(path: Path, agent="brain") -> str:
    return json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "b1", "transcript_path": str(path),
                       "cwd": str(path.parent), "agent_type": agent, "prompt": "Как дела с тикетом?"})


def test_the_brain_has_a_context_line_in_the_settings():
    assert isinstance(LINE, int) and LINE > 0


def test_past_its_line_the_brain_is_told_and_the_crossing_is_an_event(tmp_path):
    store = Store.open(tmp_path / "store")
    out, code = run_prompt_submit(payload(transcript(tmp_path, LINE + 5000)), store=store, repo="o/r")
    said = json.loads(out)["hookSpecificOutput"]
    assert code == 0 and said["hookEventName"] == "UserPromptSubmit"
    assert "hand-off" in said["additionalContext"]
    [(detail,)] = store.query("select detail from events where kind = 'context.crossed'").rows
    assert json.loads(detail) | {"tokens": 0} == {"threshold": "context_line", "tokens": 0, "action_after": "told"}


@pytest.mark.parametrize("context, agent", [(1000, "brain"), (10**6, "orch"), (10**6, None)])
def test_under_its_line_or_in_another_session_nothing_is_said(tmp_path, context, agent):
    store = Store.open(tmp_path / "store")
    assert run_prompt_submit(payload(transcript(tmp_path, context), agent), store=store, repo="o/r") == ("", 0)
    assert store.query("select count(*) from events where kind = 'context.crossed'").rows == [(0,)]


@pytest.mark.parametrize("raw", ["", "{", "[]", json.dumps({"agent_type": "brain"}),
                                 json.dumps({"agent_type": "brain", "transcript_path": "/no/such.jsonl"})])
def test_what_cannot_be_read_never_stops_a_prompt(tmp_path, raw):
    assert run_prompt_submit(raw, store=Store.open(tmp_path / "store"), repo="o/r") == ("", 0)


def test_the_repo_s_settings_run_it_on_every_prompt():
    settings = json.loads((Path(__file__).resolve().parents[1] / ".claude" / "settings.json").read_text("utf-8"))
    commands = [h["command"] for entry in settings["hooks"]["UserPromptSubmit"] for h in entry["hooks"]]
    assert commands == ["python -m orch_it hook prompt-submit"]
