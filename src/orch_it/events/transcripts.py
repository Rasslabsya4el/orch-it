"""The first writer of the store: Claude Code transcripts become events (#393 decisions 6, 7).

A transcript folder (`~/.claude/projects/<project>/`) holds one `<session id>.jsonl` per
session and, per session, `<session id>/subagents/agent-<id>.jsonl` (with a `.meta.json`) per
subagent. Each becomes a `session` or `subagent` step pair, one `turn.used` per assistant
message id (a message written over several lines counts once, at the usage of its last line),
one `tool.called` per tool use and one `skill.called` per Skill tool use. The files are read
whole every run and read-only; every event's id is a hash of its record, so a second run of
the same transcripts writes nothing, and a half-written last line is left for the next run.

A record still in motion waits: a message until a line of it carries its `stop_reason`, a tool
call until its result, a session's end until its transcript has been quiet for
`quiet_minutes` (a subagent whose meta says `finished` ends at once). Once the transcript is
quiet, what is there is written as it stands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orch_it.events import schema
from orch_it.events.ids import import_id
from orch_it.events.pricing import price_turn
from orch_it.events.store import Store

SOURCE = "claude-code"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
REFUSAL_MARKS = ("doesn't want to proceed", "tool use was rejected", "permission to use")
ARGS_HEAD_CHARS = 120


class ImportError_(Exception):
    """`code`: `no_transcript_folder`."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass
class ImportReport:
    written: int = 0
    already_stored: int = 0
    sessions: int = 0
    bad_lines: int = 0
    pending_lines: int = 0


@dataclass
class _Message:
    at: str
    model: str
    usage: dict
    order: int
    finished: bool = False


@dataclass
class _ToolUse:
    at: str
    name: str
    args: object
    turn: str
    order: int


@dataclass
class _Transcript:
    """What one transcript file says, read once."""

    session: str
    parent: str | None
    meta: dict
    first_at: str | None = None
    last_at: str | None = None
    title: str | None = None
    model: str | None = None
    effort: str | None = None
    last_stop: str | None = None
    messages: dict[str, _Message] = field(default_factory=dict)
    tool_uses: dict[str, _ToolUse] = field(default_factory=dict)
    results: dict[str, tuple[str, bool, str]] = field(default_factory=dict)
    bad_lines: int = 0
    pending_lines: int = 0


def import_transcripts(folder: str | Path, store: Store, *, repo: str, role: str = "worker",
                       subagent_role: str | None = None, ticket: int | None = None,
                       session: str | None = None, quiet_minutes: float = 30,
                       now: datetime | None = None) -> ImportReport:
    """Write the events of the transcripts in `folder` the store does not hold yet.

    `role` is the role of the top-level sessions; a subagent takes `subagent_role`, else its
    meta's `agentType` when that is a role code, else `worker`. `ticket` goes on every event.
    `session` limits the run to one top-level session and its subagents."""

    folder = Path(folder)
    if not folder.is_dir():
        raise ImportError_("no_transcript_folder", str(folder))
    now = now or datetime.now(UTC)
    report = ImportReport()
    events: dict[str, dict] = {}
    for path, parent, meta in _transcript_files(folder, session):
        transcript = _read(path, parent, meta)
        report.sessions += 1
        report.bad_lines += transcript.bad_lines
        report.pending_lines += transcript.pending_lines
        quiet = transcript.last_at is not None and (
            now - datetime.fromisoformat(transcript.last_at) >= timedelta(minutes=quiet_minutes))
        envelope = {
            "repo": repo, "ticket": ticket,
            "role": role if parent is None else _subagent_role(subagent_role, meta),
            "session": transcript.session, "parent_session": parent,
            "step": None, "run": None, "note": None,
        }
        for event in _events(transcript, envelope, quiet):
            events.setdefault(event["id"], event)
    stored = store.existing_ids(list(events))
    report.already_stored = len(stored)
    with store.writer() as writer:
        for event_id, event in events.items():
            if event_id not in stored:
                writer.write(event)
                report.written += 1
    return report


def _subagent_role(given: str | None, meta: dict) -> str:
    if given:
        return given
    agent_type = meta.get("agentType")
    return agent_type if agent_type in schema.codes_of("envelope", "role") else "worker"


def _transcript_files(folder: Path, session: str | None):
    """(path, parent session or None, meta) of every transcript, top-level sessions first."""

    for path in sorted(folder.glob("*.jsonl")):
        if session is None or path.stem == session:
            yield path, None, {}
    for path in sorted(folder.glob("*/subagents/*.jsonl")):
        parent = path.parent.parent.name
        if session is not None and parent != session:
            continue
        yield path, parent, _meta(path)


def _meta(path: Path) -> dict:
    try:
        meta = json.loads(path.with_name(path.stem + ".meta.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _local_at(timestamp: object) -> str | None:
    """A transcript timestamp (UTC) as the machine's local time with its offset."""

    if not isinstance(timestamp, str):
        return None
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone().isoformat(timespec="microseconds")


def _read(path: Path, parent: str | None, meta: dict) -> _Transcript:
    if parent is None:
        session = path.stem
    else:
        session = meta.get("agentId") or path.stem.removeprefix("agent-")
    transcript = _Transcript(session=session, parent=parent, meta=meta)
    data = path.read_bytes()
    lines = data.split(b"\n")
    if lines[-1]:
        transcript.pending_lines = 1  # a last line still being written: read next run
    for order, raw in enumerate(lines[:-1]):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except (ValueError, RecursionError):
            transcript.bad_lines += 1
            continue
        if isinstance(record, dict):
            _take(transcript, record, order)
    return transcript


def _take(transcript: _Transcript, record: dict, order: int) -> None:
    kind = record.get("type")
    if kind == "custom-title" and isinstance(record.get("customTitle"), str):
        transcript.title = record["customTitle"]
    at = _local_at(record.get("timestamp"))
    if at is None:
        return
    transcript.first_at = transcript.first_at or at
    transcript.last_at = at
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    if kind == "assistant":
        _take_assistant(transcript, record, message, blocks, at, order)
    elif kind == "user":
        for block in blocks:
            if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                text = _text_of(block.get("content"))
                transcript.results[block["tool_use_id"]] = (at, bool(block.get("is_error")), text)


def _take_assistant(transcript: _Transcript, record: dict, message: dict, blocks: list,
                    at: str, order: int) -> None:
    model, message_id = message.get("model"), message.get("id")
    if not isinstance(model, str) or model == "<synthetic>" or not isinstance(message_id, str):
        return
    transcript.model = transcript.model or model
    if isinstance(record.get("effort"), str):
        transcript.effort = transcript.effort or record["effort"]
    stop = message.get("stop_reason")
    if stop is not None:
        transcript.last_stop = stop
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
    seen = transcript.messages.get(message_id)
    if seen is None:
        seen = transcript.messages[message_id] = _Message(at, model, usage or {}, order)
    elif usage:
        seen.usage = usage  # the last line of a message holds its whole usage
    seen.finished = seen.finished or stop is not None
    for block in blocks:
        if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
            transcript.tool_uses.setdefault(block["id"], _ToolUse(
                at, str(block.get("name") or ""), block.get("input"), message_id, order))


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _count(usage: dict, key: str) -> int:
    value = usage.get(key)
    return value if type(value) is int and value > 0 else 0


def _events(transcript: _Transcript, envelope: dict, quiet: bool):
    is_subagent = transcript.parent is not None
    step = "subagent" if is_subagent else "session"
    session_record = f"session:{transcript.session}"
    if transcript.first_at is None:
        return
    started = {"model": transcript.model, "effort": transcript.effort,
               "title": transcript.meta.get("description") if is_subagent else transcript.title}
    if is_subagent:
        started["launched_by"] = "session"
        started["agent_type"] = transcript.meta.get("agentType")
    yield _event("step.started", session_record, transcript.first_at, envelope | {"step": step},
                 {k: v for k, v in started.items() if v is not None})

    for message_id, message in transcript.messages.items():
        if not (message.finished or quiet):
            continue
        yield _turn_used(message_id, message, envelope)

    first_edit = min((use.order for use in transcript.tool_uses.values() if use.name in EDIT_TOOLS),
                     default=None)
    for tool_id, use in transcript.tool_uses.items():
        result = transcript.results.get(tool_id)
        if result is None and not quiet:
            continue
        detail = {"tool": use.name, "args_head": _args_head(use.args)}
        if result is not None:
            result_at, is_error, text = result
            refused = is_error and any(mark in text.lower() for mark in REFUSAL_MARKS)
            detail["outcome"] = "refused" if refused else "failed" if is_error else "passed"
            detail["refused_by"] = "permission" if refused else None
            detail["ms"] = _ms_between(use.at, result_at)
        yield _event("tool.called", tool_id, use.at, envelope | {"turn": use.turn}, detail)
        if use.name == "Skill" and isinstance(use.args, dict) and isinstance(use.args.get("skill"), str):
            yield _event("skill.called", tool_id, use.at, envelope | {"turn": use.turn}, {
                "skill": use.args["skill"],
                "before_first_edit": first_edit is None or use.order < first_edit})

    ended = quiet or (is_subagent and transcript.meta.get("finished") is True)
    if ended:
        passed = transcript.last_stop == "end_turn" or transcript.meta.get("finished") is True
        detail = {"outcome": "passed" if passed else "no_result",
                  "reason": "turn_ended" if passed else "idle"}
        if not is_subagent and transcript.title:
            detail["title"] = transcript.title
        yield _event("step.ended", session_record, transcript.last_at, envelope | {"step": step},
                     detail)


def _turn_used(message_id: str, message: _Message, envelope: dict) -> dict:
    usage = message.usage
    cache_write = _count(usage, "cache_creation_input_tokens")
    creation = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    tokens = {
        "input_tokens": _count(usage, "input_tokens"),
        "output_tokens": _count(usage, "output_tokens"),
        "cache_read_tokens": _count(usage, "cache_read_input_tokens"),
        "cache_write_tokens": cache_write,
        "cache_write_1h_tokens": min(_count(creation, "ephemeral_1h_input_tokens"), cache_write),
    }
    usd, table = price_turn(message.model, tokens)
    detail = {"model": message.model, **tokens, "usd": usd, "price_table": table}
    if usd is None:
        detail["reason"] = "price_missing"
    return _event("turn.used", message_id, message.at, envelope | {"turn": message_id}, detail)


def _args_head(args: object) -> str:
    try:
        text = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        text = ""
    return text[:ARGS_HEAD_CHARS]


def _ms_between(start: str, end: str) -> int:
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return max(0, round(delta.total_seconds() * 1000))


def _event(kind: str, record_id: str, at: str, envelope: dict, detail: dict) -> dict:
    return {"id": import_id(SOURCE, record_id, kind), "kind": kind, "at": at,
            "turn": None, **envelope, "detail": detail}
