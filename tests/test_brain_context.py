"""The brain's context line as a check (#399 decision 2; orchestration-layer#403 row 6): on each owner
message, the brain's session hook reads the brain session's own transcript, and past the brain's
context line (a setting of the roles settings) adds a reminder to the turn and writes
`context.crossed`. The brain is found by its title, with no `agent_type`. As a sentence, the context
line was skipped in 3 of 5 brain sessions (#380)."""

import json
from pathlib import Path

import pytest

from orch_it.brain import hook
from orch_it.events.store import Store
from orch_it.roles import RoleSet, context

LINE = RoleSet.load().roles["brain"].report.context_line
REPO = "owner-x/orch-it"
TITLE = {"type": "custom-title", "customTitle": "Brain: orch-it"}
PAST = "past your context line"


@pytest.fixture(autouse=True)
def no_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))


def turns(tmp_path: Path, sizes: list[int], title: dict | None = TITLE) -> Path:
    path = tmp_path / "brain.jsonl"
    records = ([title] if title else []) + [
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
                                          "usage": {"input_tokens": 10, "cache_read_input_tokens": size}}}
        for size in sizes]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), "utf-8")
    return path


def payload(path: Path, agent=None) -> str:
    found = {"hook_event_name": "UserPromptSubmit", "session_id": "b1", "transcript_path": str(path),
             "cwd": str(path.parent), "prompt": "Как дела с тикетом?"}
    return json.dumps(found | ({"agent_type": agent} if agent else {}))


def run(tmp_path, raw, store):
    marks = tmp_path / "marks"
    marks.mkdir(exist_ok=True)
    (marks / "b1").touch()  # the rules were given at this session's start: only the context line is read
    return hook.run(raw, store=store, repo=REPO, hub=None, rules=lambda _cwd: None, marks=marks)


def test_the_brain_has_a_context_line_in_the_settings():
    assert isinstance(LINE, int) and LINE > 0


def test_past_its_line_the_brain_found_by_its_title_is_told_and_the_crossing_is_an_event(tmp_path):
    store = Store.open(tmp_path / "store")
    out, code = run(tmp_path, payload(turns(tmp_path, [LINE + 5000])), store)
    said = json.loads(out)["hookSpecificOutput"]
    assert code == 0 and said["hookEventName"] == "UserPromptSubmit"
    assert PAST in said["additionalContext"] and "hand-off" in said["additionalContext"]
    [(detail,)] = store.query("select detail from events where kind = 'context.crossed'").rows
    assert json.loads(detail) | {"tokens": 0} == {"threshold": "context_line", "tokens": 0, "action_after": "told"}


def test_the_size_is_the_latest_turn_s_so_a_compacted_brain_is_left_alone(tmp_path):
    store = Store.open(tmp_path / "store")
    assert run(tmp_path, payload(turns(tmp_path, [LINE + 50_000, 30_000])), store) == ("", 0)


def test_the_crossing_is_one_event_and_each_later_prompt_is_still_told(tmp_path):
    store = Store.open(tmp_path / "store")
    for sizes in ([LINE - 10, LINE + 100], [LINE - 10, LINE + 100, LINE + 200], [LINE - 10, LINE + 100, LINE + 300]):
        out, _ = run(tmp_path, payload(turns(tmp_path, sizes)), store)
        assert PAST in out
    assert store.query("select count(*) from events where kind = 'context.crossed'").rows == [(1,)]


def test_a_long_transcript_is_read_from_its_end(tmp_path, monkeypatch):
    path = tmp_path / "brain.jsonl"
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 40}}) + "\n"
    with path.open("w", encoding="utf-8") as out:
        out.write(json.dumps(TITLE) + "\n")
        out.write(line * 600_000)  # about 50 MB of short records
        out.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": LINE + 1}}}) + "\n")
    read = []  # the bytes the context reader takes from the transcript: a whole-file read takes about 50 MB
    real_open = open

    class Spy:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self.source.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self.source, name)

        def read(self, *args):
            data = self.source.read(*args)
            read.append(len(data))
            return data

    def spy_open(file, mode="r", *args, **kwargs):
        return Spy(real_open(file, mode, *args, **kwargs))

    monkeypatch.setattr(context, "open", spy_open, raising=False)  # the context module's own open only
    out, _ = run(tmp_path, payload(path), Store.open(tmp_path / "store"))
    assert PAST in out and 0 < sum(read) <= context.TAIL_BYTES, sum(read)


@pytest.mark.parametrize("context, title, agent", [(1000, TITLE, None), (10**6, None, "orch"),
                                                   (10**6, {"type": "custom-title", "customTitle": "Fix it"}, None)])
def test_under_its_line_or_in_another_session_nothing_is_said(tmp_path, context, title, agent):
    store = Store.open(tmp_path / "store")
    assert run(tmp_path, payload(turns(tmp_path, [context], title), agent), store) == ("", 0)
    assert store.query("select count(*) from events where kind = 'context.crossed'").rows == [(0,)]


def test_a_session_started_with_agent_brain_is_still_the_brain(tmp_path):
    out, _ = run(tmp_path, payload(turns(tmp_path, [LINE + 1], title=None), "brain"), Store.open(tmp_path / "store"))
    assert PAST in out
