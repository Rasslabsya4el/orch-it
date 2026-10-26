"""The brain's session hook (orchestration-layer#403 decision 2), `python -m orch_it hook brain`,
wired in `.claude/settings.json` at SessionStart and UserPromptSubmit. The brain is found by its
title (`orch_it.brain.session`): the repo's brain title (`repos.<owner/name>.brain_chat_title`,
default `Brain: <repo name>`) first, a session started with `--agent brain` second.

For the brain:

- its rules file (`RULES_FILE` as the default branch of `origin` holds it, never a work tree's
  edit) and the newest comment of the repo's hand-off issue (`orch_it.brain.handoff`) are added to
  the session at SessionStart (a start, a `/clear`, a compaction) and at the first prompt of a
  session that has not had them (the title may be set after the start; a `/clear` is a new
  session), with a `handoff.read` event;
- on each prompt past its context line (the roles settings' `context_line` of `brain`), the
  reminder of `orch_it.roles.context`, and `context.crossed` (`told`) at the turn that crosses it.

For an interactive chat (no subagent: no `agent_id`, no `agent_type` other than `brain`; no
background session: no `sessionKind` `bg` in its transcript's head) whose title starts with
"brain" (any case, then a colon, a dash, a space or nothing) or equals another repo's brain title,
and is not exactly this repo's: one line asking the owner, in Russian, to rename the chat to
exactly this repo's brain title, and what breaks otherwise.

It never stops a prompt or a start. A payload that is no object, a transcript that cannot be read
(a payload without one included), rules missing on the default branch, a hand-off that cannot be
read or any error of the hook lets it through, with what could be said, and writes `error.raised`
(component `brain_hook`, `failed_open`).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from orch_it import settings
from orch_it.brain import BrainSettings, default_title, handoff, handoff_issue_of, session
from orch_it.roles import RoleSet, context
from orch_it.roles.hold import error_event, write_events

RULES_FILE = "docs/agents/brain.md"
RULES_CHARS = 30_000  # past this the rules are cut, and the cut is said
COMPONENT = "brain_hook"
EVENTS = ("SessionStart", "UserPromptSubmit")
MARK_DAYS = 7  # a session's "rules given" mark older than this is removed
GIT_SECONDS = 15
_BRAIN_LIKE = re.compile(r"^\s*brain(?:\s*[:\-–—]|\s|$)", re.IGNORECASE)
_SAFE = re.compile(r"[^A-Za-z0-9_-]")
RENAME = ("This chat's title is {title!r}, not this repo's brain title. Ask the owner, in Russian, to rename this chat "
          "to exactly `{wanted}`; until then it gets no brain rules, no hand-off, no context reminder, and notices "
          "sent to the brain chat do not reach it.")


def run(raw: str, *, store=None, open_store: Callable | None = None, repo: str | None = None, hub=None,
        rules: Callable[[str], str | None] | None = None, marks: Path | None = None) -> tuple[str, int]:
    """The hook: (stdout, exit code), always exit 0. `hub`, `rules` and `marks` replace GitHub, the
    reading of the rules from `origin` and the folder of the per-session marks (tests)."""

    payload = None
    events = {"store": store, "open_store": open_store, "repo": repo, "hook": "brain"}

    def error(code: str) -> None:
        write_events(payload, lambda writer, repo_: writer.write(error_event(payload, repo_, COMPONENT, code)),
                     **events)

    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        payload = None
        error("payload_unreadable")
        return "", 0
    try:
        event = payload.get("hook_event_name")
        if event not in EVENTS:
            return "", 0
        repo = repo or _repo_of(payload)
        if repo is None:  # not a GitHub repo: no brain of the layer here
            return "", 0
        events["repo"] = repo
        raw_settings = settings.load()
        wanted = BrainSettings.of(raw_settings, repo).chat_title
        if _subagent(payload):
            return "", 0
        try:
            if session.is_brain(payload, wanted):
                said = _brain(payload, event, repo, raw_settings, hub, rules or rules_from_origin,
                              marks or settings.data_folder() / "brain-hook", error, events)
            elif event == "UserPromptSubmit":
                said = _rename(payload, repo, raw_settings, wanted)
            else:
                said = []
        except session.TranscriptUnreadable:
            error("transcript_unreadable")
            return "", 0
    except Exception as e:  # noqa: BLE001 - a broken hook never stops a prompt, and says so
        error(type(e).__name__)
        return "", 0
    if not said:
        return "", 0
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n\n".join(said)}},
                      ensure_ascii=False), 0


def _brain(payload: dict, event: str, repo: str, raw_settings: dict, hub, rules, marks: Path, error,
           events: dict) -> list[str]:
    said = []
    mark = marks / _mark_name(payload)
    if event == "SessionStart" or not mark.exists():
        said += _rules(payload, rules, error)
        said += _handoff(payload, repo, raw_settings, hub, error, events)
        _set_mark(marks, mark)
    if event == "UserPromptSubmit":
        said += _context(payload, repo, error, events)
    return said


def _rules(payload: dict, rules, error) -> list[str]:
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else "."
    text = rules(cwd)
    if not text:
        error("rules_missing")
        return [f"The brain's rules file `{RULES_FILE}` is not on the default branch of origin yet: work from the "
                "role file and say so to the owner."]
    if len(text) > RULES_CHARS:
        text = text[:RULES_CHARS] + f"\n\n[cut at {RULES_CHARS} characters: read the rest of `{RULES_FILE}`]"
    return [f"The brain's rules (`{RULES_FILE}` on the default branch):\n\n{text}"]


def _handoff(payload: dict, repo: str, raw_settings: dict, hub, error, events: dict) -> list[str]:
    number = handoff_issue_of(raw_settings, repo)
    found = None
    if number is not None:
        try:
            if hub is None:
                from orch_it.tickets.hub import GhHub

                hub = GhHub()
            found = handoff.latest(hub, repo, number)
        except Exception:  # noqa: BLE001 - the rules still go in
            error("handoff_unreadable")
            return [f"The newest hand-off (issue #{number}) could not be read: read it on GitHub."]
    detail = {"entry_ref": f"{repo}#{number}" if number else None, "found": found is not None,
              "source": "hand-off issue"}
    write_events(payload, lambda writer, repo_: writer.write(_envelope(payload, repo_) | {
        "kind": "handoff.read", "detail": detail}), **events)
    if number is None:
        return ["No hand-off issue is set for this repo: run `python -m orch_it brain handoff ensure`."]
    if found is None:
        return [f"The hand-off issue #{number} holds no hand-off yet."]
    return [f"The newest hand-off (issue #{number}, {found.at:%Y-%m-%d %H:%M} UTC):\n\n{found.body}"]


def _context(payload: dict, repo: str, error, events: dict) -> list[str]:
    role = RoleSet.load().roles.get(session.BRAIN)
    line = role.report.context_line if role and role.report else None
    path = payload.get("transcript_path")
    if line is None or not isinstance(path, str) or not path:
        return []
    try:
        past = context.reminder(path, line)
    except context.Unreadable:
        error("transcript_unreadable")
        return []
    if past is None:
        return []
    if past.crossed:
        write_events(payload, lambda writer, repo_: writer.write(_envelope(payload, repo_) | {
            "kind": "context.crossed",
            "detail": {"threshold": "context_line", "tokens": past.tokens, "action_after": "told"}}), **events)
    return [past.said]


def _rename(payload: dict, repo: str, raw_settings: dict, wanted: str) -> list[str]:
    title = session.session_title(payload)
    if title is None or title == wanted or session.is_background(payload):
        return []
    if _BRAIN_LIKE.match(title) or title in _other_titles(raw_settings, repo):
        return [RENAME.format(title=title, wanted=wanted)]
    return []


def _other_titles(raw_settings: dict, repo: str) -> set[str]:
    repos = raw_settings.get("repos") if isinstance(raw_settings.get("repos"), dict) else {}
    titles = set()
    for other in repos:
        if str(other).lower() == repo.lower():
            continue
        try:
            titles.add(BrainSettings.of(raw_settings, str(other)).chat_title)
        except settings.SettingsError:
            titles.add(default_title(str(other)))
    return titles


def _subagent(payload: dict) -> bool:
    agent = payload.get("agent_type")
    return bool(payload.get("agent_id")) or (
        isinstance(agent, str) and bool(agent) and agent.rsplit(":", 1)[-1] != session.BRAIN)


def _mark_name(payload: dict) -> str:
    key = payload.get("session_id")
    if not isinstance(key, str) or not key:
        key = str(payload.get("transcript_path"))
    cleaned = _SAFE.sub("", key)[:80]
    return cleaned if cleaned == key and cleaned else hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:32]


def _set_mark(marks: Path, mark: Path) -> None:
    try:
        marks.mkdir(parents=True, exist_ok=True)
        mark.touch()
        old = time.time() - MARK_DAYS * 86400
        for found in marks.iterdir():
            if found.is_file() and found.stat().st_mtime < old:
                found.unlink(missing_ok=True)
    except OSError:
        pass  # no mark: the next prompt is given the rules once more, never fewer


def _envelope(payload: dict, repo: str) -> dict:
    session_id = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
    return {"repo": repo, "ticket": None, "role": session.BRAIN, "session": session_id, "parent_session": None,
            "turn": None, "step": None, "run": None, "note": None}


def _repo_of(payload: dict) -> str | None:
    from orch_it.tickets.hook import repo_of

    cwd = payload.get("cwd")
    return repo_of(cwd if isinstance(cwd, str) and cwd else None)


def rules_from_origin(cwd: str) -> str | None:
    """`RULES_FILE` as the default branch of `origin` holds it (`origin/HEAD`, else `origin/main`,
    else `origin/master`), or None. Read from git's objects: a work tree's own edit is never read."""

    from orch_it.testruns.runner import NO_WINDOW

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=GIT_SECONDS,
                                  creationflags=NO_WINDOW)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
        return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None

    head = (git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD") or "").strip()
    for ref in dict.fromkeys(name for name in (head, "origin/main", "origin/master") if name):
        text = git("show", f"{ref}:{RULES_FILE}")
        if text is not None:
            return text
    return None
