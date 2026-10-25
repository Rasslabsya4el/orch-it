"""The transcript importer (#393 decisions 6, 7; rows 4, 5): a Claude Code transcript folder
becomes session and subagent step pairs, turn.used, tool.called and skill.called events.
Every expected number is a hand sum of the fixture below."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orch_it.events.store import Store
from orch_it.events.transcripts import import_transcripts

TOP = "11111111-aaaa-bbbb-cccc-000000000001"
AGENT = "a0c0ffee0000000001"
LATER = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)  # long after the fixture: quiet
DURING = datetime(2026, 10, 25, 18, 5, tzinfo=timezone.utc)  # a minute after its last line


def assistant(mid, ts, model, usage, content, stop="tool_use", **extra):
    return {"type": "assistant", "sessionId": TOP, "timestamp": ts, "effort": "high",
            "message": {"id": mid, "model": model, "role": "assistant", "stop_reason": stop,
                        "usage": usage, "content": content}, **extra}


def usage(i, o, cr, cw, cw1h=0):
    return {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cw,
            "cache_creation": {"ephemeral_1h_input_tokens": cw1h, "ephemeral_5m_input_tokens": cw - cw1h}}


def tool_use(tid, name, args):
    return {"type": "tool_use", "id": tid, "name": name, "input": args}


def result(tid, ts, text="ok", error=False):
    return {"type": "user", "sessionId": TOP, "timestamp": ts,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}}


def top_lines():
    u1 = usage(10, 100, 1000, 200, 200)
    return [
        {"type": "custom-title", "sessionId": TOP, "customTitle": "event store"},
        {"type": "user", "sessionId": TOP, "timestamp": "2026-10-25T18:00:00.000Z",
         "message": {"role": "user", "content": "build the store"}},
        # one message over three lines: counted once
        assistant("msg_1", "2026-10-25T18:00:05.000Z", "claude-opus-5-5", u1,
                  [{"type": "thinking", "thinking": "."}]),
        assistant("msg_1", "2026-10-25T18:00:06.000Z", "claude-opus-5-5", u1,
                  [tool_use("tu_skill", "Skill", {"skill": "mattpocock-skills:tdd"})]),
        assistant("msg_1", "2026-10-25T18:00:06.500Z", "claude-opus-5-5", u1,
                  [tool_use("tu_bash", "Bash", {"command": "x" * 300})]),
        result("tu_skill", "2026-10-25T18:00:07.000Z"),
        result("tu_bash", "2026-10-25T18:00:09.000Z", "exit 1", error=True),
        assistant("msg_2", "2026-10-25T18:01:00.000Z", "claude-opus-5-5", usage(5, 50, 2000, 0),
                  [tool_use("tu_edit", "Edit", {"file_path": "a.py"})]),
        result("tu_edit", "2026-10-25T18:01:01.000Z",
               "The user doesn't want to proceed with this tool use. The tool use was rejected.",
               error=True),
        assistant("msg_3", "2026-10-25T18:02:00.000Z", "claude-opus-5-5", usage(1, 1, 0, 0),
                  [tool_use("tu_skill2", "Skill", {"skill": "mattpocock-skills:code-review"})]),
        result("tu_skill2", "2026-10-25T18:02:01.000Z"),
        # a synthetic message is no model turn
        assistant("msg_syn", "2026-10-25T18:03:00.000Z", "<synthetic>", usage(0, 0, 0, 0), []),
        assistant("msg_4", "2026-10-25T18:04:00.000Z", "claude-opus-5-5", usage(2, 20, 0, 0),
                  [{"type": "text", "text": "done"}], stop="end_turn"),
    ]


def agent_lines():
    return [
        {"type": "user", "sessionId": TOP, "agentId": AGENT, "isSidechain": True,
         "timestamp": "2026-10-25T18:00:30.000Z",
         "message": {"role": "user", "content": "write the doc"}},
        assistant("msg_a1", "2026-10-25T18:00:40.000Z", "claude-haiku-5-5", usage(3, 30, 0, 100),
                  [{"type": "text", "text": "ok"}], stop="end_turn", agentId=AGENT),
        assistant("msg_a2", "2026-10-25T18:00:50.000Z", "claude-mystery-1", usage(7, 7, 0, 0),
                  [{"type": "text", "text": "?"}], stop="end_turn", agentId=AGENT),
    ]


def write_lines(path: Path, lines, tail: bytes = b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(json.dumps(line).encode() + b"\n" for line in lines) + tail)


@pytest.fixture
def folder(tmp_path):
    project = tmp_path / "projects" / "C--Coding-orch-it"
    write_lines(project / f"{TOP}.jsonl", top_lines())
    subagents = project / TOP / "subagents"
    write_lines(subagents / f"agent-{AGENT}.jsonl", agent_lines())
    (subagents / f"agent-{AGENT}.meta.json").write_text(json.dumps(
        {"agentType": "worker", "description": "write the doc", "finished": True}))
    return project


def run(folder, store, now=LATER, **over):
    options = {"repo": "Rasslabsya4el/orch-it", "role": "worker-strong", "ticket": 393, "now": now}
    return import_transcripts(folder, store, **options | over)


def test_tokens_per_model_equal_the_hand_sum(folder, tmp_path):
    store = Store.open(tmp_path / "store")
    run(folder, store)
    rows = store.query(
        "SELECT model, count(*), sum(input_tokens), sum(output_tokens), sum(cache_read_tokens),"
        " sum(cache_write_tokens) FROM events WHERE kind = 'turn.used'"
        " GROUP BY model ORDER BY model").rows
    assert rows == [
        ("claude-haiku-5-5", 1, 3, 30, 0, 100),
        ("claude-mystery-1", 1, 7, 7, 0, 0),
        ("claude-opus-5-5", 4, 18, 171, 3000, 200),
    ]


def test_dollars_are_priced_per_turn(folder, tmp_path):
    store = Store.open(tmp_path / "store")
    run(folder, store)
    usd = dict(store.query("SELECT turn, usd FROM events WHERE kind = 'turn.used'").rows)
    # msg_1: 10 x 4 + 100 x 20 + 1000 x 0.2 + 200 (1h) x 8 = 3840 per million
    assert round(usd["msg_1"], 9) == 0.00384
    # msg_2: 5 x 4 + 50 x 20 + 2000 x 0.2 = 1420 per million
    assert round(usd["msg_2"], 9) == 0.00142


def test_a_model_missing_from_the_price_file_writes_no_number(folder, tmp_path):
    store = Store.open(tmp_path / "store")
    run(folder, store)
    rows = store.query("SELECT usd, reason, json_type(detail, '$.usd') FROM events"
                       " WHERE kind = 'turn.used' AND model = 'claude-mystery-1'").rows
    assert rows == [(None, "price_missing", "null")]


def test_importing_the_same_transcripts_again_adds_nothing(folder, tmp_path):
    store = Store.open(tmp_path / "store")
    first = run(folder, store)
    before = store.query("SELECT count(*) FROM events").rows
    second = run(folder, store)
    assert first.written > 0 and second.written == 0
    assert second.already_stored == first.written
    assert store.query("SELECT count(*) FROM events").rows == before


def test_a_half_written_last_line_waits_for_the_next_run(folder, tmp_path):
    path = folder / f"{TOP}.jsonl"
    lines = top_lines()
    last = json.dumps(lines[-1]).encode()
    write_lines(path, lines[:-1], tail=last[:30])
    store = Store.open(tmp_path / "store")
    report = run(folder, store, now=DURING)
    assert (report.pending_lines, report.bad_lines) == (1, 0)
    assert store.query("SELECT count(*) FROM events WHERE turn = 'msg_4'").rows == [(0,)]
    write_lines(path, lines)
    run(folder, store, now=DURING)
    assert store.query(
        "SELECT count(*) FROM events WHERE kind = 'turn.used' AND turn = 'msg_4'").rows == [(1,)]


def test_a_message_still_streaming_waits_until_its_final_line(folder, tmp_path):
    path = folder / f"{TOP}.jsonl"
    partial = assistant("msg_5", "2026-10-25T18:04:30.000Z", "claude-opus-5-5",
                        usage(1, 3, 0, 0), [], stop=None)
    write_lines(path, top_lines() + [partial])
    store = Store.open(tmp_path / "store")
    run(folder, store, now=DURING)
    assert store.query("SELECT count(*) FROM events WHERE turn = 'msg_5'").rows == [(0,)]
    final = assistant("msg_5", "2026-10-25T18:04:31.000Z", "claude-opus-5-5", usage(1, 40, 0, 0),
                      [{"type": "text", "text": "x"}], stop="end_turn")
    write_lines(path, top_lines() + [partial, final])
    run(folder, store, now=DURING)
    assert store.query("SELECT output_tokens FROM events WHERE turn = 'msg_5'").rows == [(40,)]


def test_a_quiet_transcript_s_unfinished_message_is_counted_as_it_stands(folder, tmp_path):
    path = folder / f"{TOP}.jsonl"
    partial = assistant("msg_5", "2026-10-25T18:04:30.000Z", "claude-opus-5-5",
                        usage(1, 3, 0, 0), [], stop=None)
    write_lines(path, top_lines() + [partial])
    store = Store.open(tmp_path / "store")
    run(folder, store, now=LATER)
    assert store.query("SELECT output_tokens FROM events WHERE turn = 'msg_5'").rows == [(3,)]


def test_sessions_are_step_pairs_and_a_subagent_has_its_parent(folder, tmp_path):
    store = Store.open(tmp_path / "store")
    run(folder, store)
    rows = store.query(
        "SELECT kind, step, session, parent_session, role, outcome, reason, at FROM events"
        " WHERE kind LIKE 'step.%' ORDER BY step, kind").rows
    assert rows == [
        ("step.ended", "session", TOP, None, "worker-strong", "passed", "turn_ended",
         "2026-10-25T18:04:00.000000Z"),
        ("step.started", "session", TOP, None, "worker-strong", None, None,
         "2026-10-25T18:00:00.000000Z"),
        ("step.ended", "subagent", AGENT, TOP, "worker", "passed", "turn_ended",
         "2026-10-25T18:00:50.000000Z"),
        ("step.started", "subagent", AGENT, TOP, "worker", None, None,
         "2026-10-25T18:00:30.000000Z"),
    ]
    started = dict(store.query("SELECT session, detail FROM events WHERE kind = 'step.started'").rows)
    assert json.loads(started[TOP])["title"] == "event store"
    assert json.loads(started[TOP])["model"] == "claude-opus-5-5"
    assert json.loads(started[AGENT])["title"] == "write the doc"
    assert json.loads(started[AGENT])["launched_by"] == "session"
    assert store.query("SELECT DISTINCT ticket, repo FROM events").rows == [
        (393, "Rasslabsya4el/orch-it")]


def test_a_live_session_has_no_end_yet(folder, tmp_path):
    (folder / TOP / "subagents" / f"agent-{AGENT}.meta.json").write_text(
        json.dumps({"agentType": "worker"}))
    store = Store.open(tmp_path / "store")
    run(folder, store, now=DURING)
    assert store.query("SELECT count(*) FROM events WHERE kind = 'step.ended'").rows == [(0,)]
    assert store.query("SELECT count(*) FROM events WHERE kind = 'step.started'").rows == [(2,)]


def test_a_subagent_role_given_wins_over_its_agent_type(folder, tmp_path):
    store = Store.open(tmp_path / "store")
    run(folder, store, subagent_role="reviewer-spec")
    assert store.query("SELECT DISTINCT role FROM events WHERE session = ?", (AGENT,)).rows == [
        ("reviewer-spec",)]


def test_tool_calls_carry_a_short_head_their_outcome_and_time(folder, tmp_path):
    store = Store.open(tmp_path / "store")
    run(folder, store)
    rows = store.query("SELECT tool, args_head, outcome, refused_by, ms, turn FROM events"
                       " WHERE kind = 'tool.called' ORDER BY at").rows
    assert rows == [
        ("Skill", '{"skill":"mattpocock-skills:tdd"}', "passed", None, 1000, "msg_1"),
        ("Bash", '{"command":"' + "x" * 108, "failed", None, 2500, "msg_1"),
        ("Edit", '{"file_path":"a.py"}', "refused", "permission", 1000, "msg_2"),
        ("Skill", '{"skill":"mattpocock-skills:code-review"}', "passed", None, 1000, "msg_3"),
    ]


def test_skill_calls_say_whether_they_came_before_the_first_edit(folder, tmp_path):
    store = Store.open(tmp_path / "store")
    run(folder, store)
    rows = store.query("SELECT skill, before_first_edit FROM events"
                       " WHERE kind = 'skill.called' ORDER BY at").rows
    assert rows == [("mattpocock-skills:tdd", 1), ("mattpocock-skills:code-review", 0)]


def test_one_session_can_be_imported_alone(folder, tmp_path):
    other = "22222222-aaaa-bbbb-cccc-000000000002"
    write_lines(folder / f"{other}.jsonl", [{**line, "sessionId": other} for line in top_lines()[:2]])
    store = Store.open(tmp_path / "store")
    report = run(folder, store, session=TOP)
    assert report.sessions == 2
    assert store.query("SELECT DISTINCT session FROM events ORDER BY session").rows == [
        (TOP,), (AGENT,)]


def test_a_broken_line_is_counted_and_skipped(folder, tmp_path):
    path = folder / f"{TOP}.jsonl"
    path.write_bytes(b"not json\n" + path.read_bytes())
    store = Store.open(tmp_path / "store")
    assert run(folder, store).bad_lines == 1
    assert store.query("SELECT count(*) FROM events WHERE kind = 'turn.used'").rows == [(6,)]


def test_a_folder_that_is_no_transcript_folder_is_refused(tmp_path):
    from orch_it.events.transcripts import ImportError_
    store = Store.open(tmp_path / "store")
    with pytest.raises(ImportError_) as caught:
        run(tmp_path / "missing", store)
    assert caught.value.code == "no_transcript_folder"
