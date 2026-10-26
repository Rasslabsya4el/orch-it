"""Who a session is (orchestration-layer#403 decision 2): its title, read as the old layer read it,
and whether it is the brain.

- `session_title(payload)`: the payload's `session_title`, else the `customTitle` of the last
  `custom-title` record in the session's transcript (a rename writes a new record, so the last one
  wins): the last `TAIL_BYTES` of the file first, the whole file only when they hold none. A
  half-written or broken line is skipped. A transcript that cannot be read raises
  `TranscriptUnreadable`.
- `is_brain(payload, brain_title)`: the title equals the repo's brain title exactly (the key), else
  the session was started with `--agent brain` (the second key): the payload's `agent_type` (a
  plugin's `plugin:brain` too), or the `agentSetting` its transcript head records, since a main
  session started with `--agent` carries no `agent_type` in its payload.
"""

from __future__ import annotations

import json
import os

TAIL_BYTES = 256 * 1024  # the newest title record sits near the end: a rename appends one
HEAD_LINES = 50  # the launch facts a transcript records first
BRAIN = "brain"


class TranscriptUnreadable(Exception):
    """The session's transcript is not named or cannot be read."""


def session_title(payload: dict) -> str | None:
    title = payload.get("session_title")
    if isinstance(title, str) and title:
        return title
    return _last_title(_path(payload))


def is_brain(payload: dict, brain_title: str) -> bool:
    """See the module text; a transcript that cannot be read raises `TranscriptUnreadable`."""

    if session_title(payload) == brain_title:
        return True
    agent = payload.get("agent_type")
    if isinstance(agent, str) and agent:
        return agent.rsplit(":", 1)[-1] == BRAIN
    path = payload.get("transcript_path")
    return isinstance(path, str) and bool(path) and _agent_setting(path) == BRAIN


def _path(payload: dict) -> str:
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        raise TranscriptUnreadable("no transcript path")
    return path


def _last_title(path: str) -> str | None:
    try:
        with open(path, "rb") as source:
            size = source.seek(0, os.SEEK_END)
            source.seek(max(0, size - TAIL_BYTES))
            tail = source.read()
            if size > TAIL_BYTES:  # the cut landed inside a line: that first piece is no record
                tail = tail.split(b"\n", 1)[1] if b"\n" in tail else b""
            title = _title_in(tail.split(b"\n"))
            if title is None and size > TAIL_BYTES:
                source.seek(0)
                title = _title_in(source)
    except (OSError, ValueError) as e:
        raise TranscriptUnreadable(type(e).__name__) from None
    return title


def _title_in(lines) -> str | None:
    title = None
    for raw in lines:
        if b'"custom-title"' not in raw:
            continue
        record = _record(raw)
        if record.get("type") == "custom-title" and isinstance(record.get("customTitle"), str) and record["customTitle"]:
            title = record["customTitle"]
    return title


def _agent_setting(path: str) -> str | None:
    try:
        with open(path, "rb") as source:
            for number, raw in enumerate(source):
                if number >= HEAD_LINES:
                    break
                if b"agentSetting" in raw:
                    value = _record(raw).get("agentSetting")
                    if isinstance(value, str) and value:
                        return value
    except (OSError, ValueError) as e:
        raise TranscriptUnreadable(type(e).__name__) from None
    return None


def _record(raw: bytes) -> dict:
    try:
        record = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    return record if isinstance(record, dict) else {}
