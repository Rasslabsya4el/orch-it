"""The context line of a session as a check (#399 decision 2): a Claude Code UserPromptSubmit hook,
`python -m orch_it hook prompt-submit`. On each prompt of a session whose role (the payload's
`agent_type`, which Claude Code sets for a session started with `--agent <role>`) has a
`context_line` in the roles settings and no inputs (a session, not a subagent: the brain), it reads
the session's own transcript; past the line it adds a reminder to the turn (`additionalContext`) and
writes `context.crossed` (threshold `context_line`, `action_after` `told`). The size is the largest
prompt of any turn, as the stop hold reads it (`orch_it.roles.hold.largest_prompt`).

Anything it cannot read (a payload that is no object, no transcript) lets the prompt through with
nothing said; the hook never blocks a prompt. With no GitHub repo around `cwd`, or a store that
cannot be written, nothing is written and the reminder still goes out.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from orch_it.roles import RoleSet
from orch_it.roles.hold import TranscriptUnreadable, largest_prompt, transcript_records

REMINDER = ("Your context is past your context line ({tokens} of {line} tokens). Answer the owner in full and finish"
            " the step in hand; then write the hand-off comment on the ticket in work (what is done, what comes next,"
            " the question pending) and start no new ticket work in this chat.")


def run_prompt_submit(raw: str, *, store=None, open_store: Callable | None = None,
                      repo: str | None = None) -> tuple[str, int]:
    """The hook: (stdout, exit code), always exit 0."""

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return "", 0
        agent = payload.get("agent_type")
        role = RoleSet.load().roles.get(agent.rsplit(":", 1)[-1]) if isinstance(agent, str) else None
        if role is None or role.inputs or role.report is None or role.report.context_line is None:
            return "", 0
        path = payload.get("transcript_path")
        if not isinstance(path, str) or not path:
            return "", 0
        tokens = largest_prompt(transcript_records(path))
        if tokens <= role.report.context_line:
            return "", 0
    except (TranscriptUnreadable, ValueError, RecursionError):
        return "", 0
    _record(payload, role.name, tokens, store, open_store, repo)
    said = REMINDER.format(tokens=tokens, line=role.report.context_line)
    return json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": said}}), 0


def _record(payload: dict, role: str, tokens: int, store, open_store, repo) -> None:
    try:
        if repo is None:
            from orch_it.tickets.hook import repo_of

            repo = repo_of(payload.get("cwd") if isinstance(payload.get("cwd"), str) else None)
        if repo is None:
            return
        if store is None:
            from orch_it.events.store import Store

            store = (open_store or Store.open)()
        session = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
        with store.writer() as writer:
            writer.write({"kind": "context.crossed", "repo": repo, "ticket": None, "role": role, "session": session,
                          "parent_session": None, "turn": None, "step": None, "run": None, "note": None,
                          "detail": {"threshold": "context_line", "tokens": tokens, "action_after": "told"}})
    except Exception as e:  # noqa: BLE001 - the reminder goes out whatever the store does
        print(f"prompt-submit hook: event not written: {type(e).__name__}: {e}", file=sys.stderr)
