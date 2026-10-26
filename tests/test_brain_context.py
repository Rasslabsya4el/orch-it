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


def turns(tmp_path: Path, sizes: list[int]) -> Path:
    path = tmp_path / "brain.jsonl"
    records = [{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
                                                 "usage": {"input_tokens": 10, "cache_read_input_tokens": size}}}
               for size in sizes]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), "utf-8")
    return path


def test_the_size_is_the_latest_turn_s_so_a_compacted_brain_is_left_alone(tmp_path):
    store = Store.open(tmp_path / "store")
    assert run_prompt_submit(payload(turns(tmp_path, [LINE + 50_000, 30_000])), store=store, repo="o/r") == ("", 0)


def test_the_crossing_is_one_event_and_each_later_prompt_is_still_told(tmp_path):
    store = Store.open(tmp_path / "store")
    for sizes in ([LINE - 10, LINE + 100], [LINE - 10, LINE + 100, LINE + 200], [LINE - 10, LINE + 100, LINE + 300]):
        out, _ = run_prompt_submit(payload(turns(tmp_path, sizes)), store=store, repo="o/r")
        assert "hand-off" in out
    assert store.query("select count(*) from events where kind = 'context.crossed'").rows == [(1,)]


def test_a_long_transcript_is_read_from_its_end(tmp_path):
    import time

    path = tmp_path / "brain.jsonl"
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 40}}) + "\n"
    with path.open("w", encoding="utf-8") as out:
        out.write(line * 600_000)  # about 50 MB of short records
        out.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": LINE + 1}}}) + "\n")
    started = time.monotonic()
    out, _ = run_prompt_submit(payload(path), store=Store.open(tmp_path / "store"), repo="o/r")
    assert "hand-off" in out and time.monotonic() - started < 0.5


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
