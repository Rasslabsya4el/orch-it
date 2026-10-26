"""The context line of a session as a check (#399 decision 2): a Claude Code UserPromptSubmit hook,
`python -m orch_it hook prompt-submit`. On each prompt of a session whose role (the payload's
`agent_type`, which Claude Code sets for a session started with `--agent <role>`) is a session (no
inputs: the brain) with a `context_line` in the roles settings, it reads the end of the session's
own transcript. The session's size is its latest turn's prompt (input, cache read and cache write
tokens), so a session that compacted is measured as it is now. Past the line it adds a reminder to
the turn (`additionalContext`); the turn that first passes it (the turn before was under it) also
writes `context.crossed` (threshold `context_line`, `action_after` `told`), so the event counts
crossings, not messages.

It never stops a prompt. A payload that is no object or a transcript that cannot be read lets the
prompt through with nothing said and an `error.raised` (`prompt_submit`, `payload_unreadable`,
`transcript_unreadable`, `failed_open`); a store that cannot be written never stops the reminder.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from orch_it.roles import RoleSet
from orch_it.roles.hold import error_event, role_of, write_events

TAIL_BYTES = 4 * 1024 * 1024  # the end of the transcript read for its last turns
REMINDER = ("Your context is past your context line ({tokens} of {line} tokens). Answer the owner in full and finish"
            " the step in hand; then write the hand-off comment and leave the next ticket work to the next chat.")


class _Unreadable(Exception):
    pass


def _prompt_sizes(path: str) -> list[int]:
    """The prompt size of each of the last turns that carry usage, oldest first, read from the end
    of the transcript; a half-written or broken line is skipped."""

    try:
        with open(path, "rb") as source:
            size = os.fstat(source.fileno()).st_size
            source.seek(max(0, size - TAIL_BYTES))
            tail = source.read()
    except (OSError, ValueError) as e:
        raise _Unreadable(type(e).__name__) from None
    lines = tail.split(b"\n")
    if size > TAIL_BYTES:
        lines = lines[1:]  # the first line of the tail is cut
    sizes = []
    for line in lines:
        if b"usage" not in line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            continue
        message = record.get("message") if isinstance(record, dict) else None
        usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(usage, dict):
            parts = [usage.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
            sizes.append(sum(p for p in parts if type(p) is int and p > 0))
    return sizes


def run_prompt_submit(raw: str, *, store=None, open_store: Callable | None = None,
                      repo: str | None = None) -> tuple[str, int]:
    """The hook: (stdout, exit code), always exit 0."""

    payload = None
    events = {"store": store, "open_store": open_store, "repo": repo, "hook": "prompt-submit"}

    def failed(code: str) -> tuple[str, int]:
        write_events(payload, lambda writer, repo_: writer.write(error_event(payload, repo_, "prompt_submit", code)),
                     **events)
        return "", 0

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return failed("payload_unreadable")
        role = role_of(payload, RoleSet.load())
        if role is None or not role.is_session or role.report is None or role.report.context_line is None:
            return "", 0
        path = payload.get("transcript_path")
        if not isinstance(path, str) or not path:
            return failed("transcript_unreadable")
        sizes = _prompt_sizes(path)
    except _Unreadable:
        return failed("transcript_unreadable")
    except Exception as e:  # noqa: BLE001 - a broken check never stops a prompt, and says so
        return failed(type(e).__name__)
    line = role.report.context_line
    if not sizes or sizes[-1] <= line:
        return "", 0
    if len(sizes) == 1 or sizes[-2] <= line:  # this turn is the one that crossed
        crossed = {"kind": "context.crossed", "detail": {"threshold": "context_line", "tokens": sizes[-1],
                                                         "action_after": "told"}}
        write_events(payload, lambda writer, repo_: writer.write(_envelope(payload, repo_, role.name) | crossed),
                     **events)
    said = REMINDER.format(tokens=sizes[-1], line=line)
    return json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": said}}), 0


def _envelope(payload: dict, repo: str, role: str) -> dict:
    session = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
    return {"repo": repo, "ticket": None, "role": role, "session": session, "parent_session": None, "turn": None,
            "step": None, "run": None, "note": None}
