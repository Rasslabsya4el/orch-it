"""The session hook on `gh issue create` and `gh issue edit` (#398 decision 3, the session side;
decision 4 for the park): a Claude Code PreToolUse hook on the Bash and PowerShell tools.

Refused, with every reason at once:

- `write_ticket_missing`: a `gh issue create` in a session whose transcript holds no call of the
  `write-ticket` skill (a Skill tool use, or the `/write-ticket` command);
- the shape rules' codes (`shape.violations`): the body a create writes, the body an edit
  writes, or, for an edit that only adds `ready-for-agent`, the body the ticket already holds;
  for an edit of the title alone, the title rules;
- `park_by_hand`: a create whose body holds a `Parked:` line or that sets the `parked` label; an
  edit that adds or removes the `parked` label, or whose body adds, changes or removes the
  ticket's `Parked:` line (only `python -m orch_it tickets park` and `unpark` write them);
- `body_unreadable`: a body the hook cannot read before `gh` does (stdin, an editor, a file that
  is not there, a word built by a substitution or a variable): write it to a file and pass
  `--body-file` with its path;
- `command_unreadable`: another word of the command it cannot read (title, label, number);
- `issue_unreadable`: an edit whose ticket cannot be read from GitHub to judge it.

The command is read by part 3's `orch_it.guards.shell.simple_commands` (POSIX or Windows, through
`cd`, wrappers and compound commands). Each judged command writes a `hook.ran` event (`acted`
with the first reason and every code in `rules`, or `skipped`/`passed`). An error of the hook
itself lets the command through (`error.raised`, `failed_open`): a broken hook must not stop a
session, and the GitHub workflow judges a ready ticket again (decision 3).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HOOK = "tickets"
WRITE_TICKET = "write-ticket"
HOOK_CODES = ("write_ticket_missing", "park_by_hand", "body_unreadable", "command_unreadable", "issue_unreadable")
_QUICK = re.compile(r"\bgh(?:\.exe)?\b.*\bissue\b", re.IGNORECASE | re.DOTALL)
_ISSUE_URL = re.compile(r"https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/(?:issues|pull)/([1-9][0-9]*)/?")
_ORIGIN = re.compile(r"(?:github\.com[:/])([A-Za-z0-9-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
# gh's flags that take a value, by subcommand; any other flag is a switch
_VALUE_FLAGS = {
    "create": {"-t": "title", "--title": "title", "-b": "body", "--body": "body", "-F": "body_file",
               "--body-file": "body_file", "-l": "label", "--label": "label", "-R": "repo", "--repo": "repo",
               "-a": "", "--assignee": "", "-m": "", "--milestone": "", "-p": "", "--project": "",
               "-T": "", "--template": "", "--recover": ""},
    "edit": {"-t": "title", "--title": "title", "-b": "body", "--body": "body", "-F": "body_file",
             "--body-file": "body_file", "--add-label": "label", "--remove-label": "unlabel", "-R": "repo",
             "--repo": "repo", "--add-assignee": "", "--remove-assignee": "", "--add-project": "",
             "--remove-project": "", "-m": "", "--milestone": ""},
}
_EDITOR_FLAGS = {"-e", "--editor", "-w", "--web"}


@dataclass(frozen=True)
class Refusal:
    codes: list[str]  # every reason, in order: the hook's own, then the shape rules'; none: it passed
    message: str
    repo: str | None

    def __bool__(self) -> bool:
        return bool(self.codes)


def judge(payload: dict, *, hub, repo_of) -> Refusal | None:
    """None when the tool call in `payload` writes no issue; else its `Refusal`, which is false
    (no codes) when the issue it writes is good. `repo_of(folder)` gives a work tree's GitHub
    `owner/name`, or None."""

    command = (payload.get("tool_input") or {}).get("command") if isinstance(payload, dict) else None
    if payload.get("tool_name") not in ("Bash", "PowerShell") or not isinstance(command, str):
        return None
    if not _QUICK.search(command):
        return None
    from orch_it.guards.shell import simple_commands

    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    commands = simple_commands(command, tool=payload["tool_name"], cwd=cwd, env=os.environ,
                               windows=sys.platform == "win32")
    judged = [_Call.of(simple) for simple in _flatten(commands)]
    judged = [call for call in judged if call is not None]
    if not judged:
        return None
    codes: list[str] = []
    said: list[str] = []
    repo = None
    for call in judged:
        found, lines, repo = call.judge(payload, hub, repo_of, cwd)
        codes += [code for code in found if code not in codes]
        said += [line for line in lines if line not in said]
    return Refusal(codes, "\n".join(said), repo)


def _flatten(commands):
    for simple in commands:
        yield simple
        yield from _flatten(simple.subs)


@dataclass
class _Call:
    action: str  # create | edit
    words: list[str | None]
    cwd: str | None

    @classmethod
    def of(cls, simple) -> _Call | None:
        argv = simple.argv
        if len(argv) < 3 or not isinstance(argv[0], str):
            return None
        if re.split(r"[\\/]", argv[0])[-1].lower() not in ("gh", "gh.exe"):
            return None
        if argv[1] != "issue" or argv[2] not in ("create", "edit"):
            return None
        return cls(argv[2], argv[3:], simple.cwd)

    def judge(self, payload: dict, hub, repo_of, cwd: str | None) -> tuple[list[str], list[str], str | None]:
        from orch_it.tickets import shape
        from orch_it.tickets.model import PARKED_LABEL, READY_LABEL, parse

        flags, targets, unreadable = self._read()
        here = self.cwd or cwd
        repo = flags.get("repo", [None])[-1] or next((t[0] for t in targets if t and t[0]), None)
        if unreadable:
            return ["command_unreadable"], [f"command_unreadable: {unreadable}: write it as a literal"], repo
        body, problem = self._body(flags, here)
        title = flags.get("title", [None])[-1]
        labels = {label.strip().lower() for value in flags.get("label", []) for label in value.split(",")}
        unlabels = {label.strip().lower() for value in flags.get("unlabel", []) for label in value.split(",")}
        codes, lines = [], []

        def refuse(code: str, line: str) -> None:
            codes.append(code)
            lines.append(f"{code}: {line}")

        if problem:
            refuse("body_unreadable", f"{problem}; write the body to a file and pass --body-file with its path")
        if self.action == "create":
            if not _write_ticket_called(payload.get("transcript_path")):
                refuse("write_ticket_missing", f"call the Skill tool with `{WRITE_TICKET}` before `gh issue create`, "
                                               "then write the ticket again")
            if PARKED_LABEL in labels or (body is not None and parse(body).park_lines):
                refuse("park_by_hand", "a park is written only by `python -m orch_it tickets park`")
            if not problem:
                for violation in shape.violations(parse(body or "", title=title)):
                    refuse(violation.code, violation.message.split(": ", 1)[1])
            return codes, lines, repo or repo_of(here)
        if PARKED_LABEL in labels | unlabels:
            refuse("park_by_hand", "the `parked` label is set and taken off only by `tickets park` and `unpark`")
        needs_ticket = body is not None or READY_LABEL in labels
        if not needs_ticket:
            if title is not None:
                for violation in shape.title_violations(title):
                    refuse(violation.code, violation.message.split(": ", 1)[1])
            return codes, lines, repo
        repo = repo or repo_of(here)
        for target_repo, number in targets:
            current = _current(hub, target_repo or repo, number)
            if current is None:
                refuse("issue_unreadable", f"#{number} could not be read from GitHub to judge the edit")
                continue
            if body is not None and parse(body).park_lines != parse(current.body).park_lines:
                refuse("park_by_hand", "the `Parked:` line is written and taken off only by `tickets park` and "
                                       "`unpark`; keep it as it is")
            if problem:
                continue
            judged = parse(body if body is not None else current.body,
                           title=title if title is not None else current.title)
            for violation in shape.violations(judged):
                refuse(violation.code, violation.message.split(": ", 1)[1])
        return list(dict.fromkeys(codes)), list(dict.fromkeys(lines)), repo

    def _read(self) -> tuple[dict[str, list[str]], list[tuple[str | None, int]], str | None]:
        """(flag values by meaning, edit targets, the first word that could not be read)."""

        known = _VALUE_FLAGS[self.action]
        flags: dict[str, list[str]] = {}
        targets: list[tuple[str | None, int]] = []
        words = list(self.words)
        i = 0
        while i < len(words):
            word = words[i]
            if word is None:
                return flags, targets, "a word built by a substitution or a variable"
            name, value = word, None
            if word.startswith("--") and "=" in word:
                name, value = word.split("=", 1)
            elif len(word) > 2 and word.startswith("-") and not word.startswith("--") and word[:2] in known:
                name, value = word[:2], word[2:]
            if name in _EDITOR_FLAGS:
                flags.setdefault("body_file", []).append("-")  # an editor or the browser: unreadable here
            elif name in known:
                if value is None:
                    i += 1
                    value = words[i] if i < len(words) else None
                    if i < len(words) and value is None:
                        if known[name] not in ("body", "body_file"):
                            return flags, targets, f"the value of {name}"
                        flags.setdefault("body_file", []).append("-")  # a body built by the shell
                if known[name] and value is not None:
                    flags.setdefault(known[name], []).append(value)
            elif not word.startswith("-") and self.action == "edit":
                found = _ISSUE_URL.fullmatch(word)
                if found:
                    targets.append((found.group(1), int(found.group(2))))
                elif word.isdigit():
                    targets.append((None, int(word)))
                elif word.startswith("#") and word[1:].isdigit():
                    targets.append((None, int(word[1:])))
            i += 1
        return flags, targets, None

    def _body(self, flags: dict[str, list[str]], here: str | None) -> tuple[str | None, str | None]:
        """(the body the command writes, or None when it writes none; the reason it is unreadable)."""

        if "body_file" in flags:
            name = flags["body_file"][-1]
            if name == "-":
                return None, "the body comes from stdin, an editor, the browser or a word the shell builds"
            path = Path(name)
            if not path.is_absolute():
                path = Path(here or os.getcwd()) / path
            try:
                return path.read_text("utf-8"), None
            except (OSError, UnicodeDecodeError) as e:
                return None, f"the body file {name} cannot be read ({type(e).__name__})"
        if "body" in flags:
            return flags["body"][-1], None
        return None, None


def _current(hub, repo: str | None, number: int):
    from orch_it.testruns.github import GithubError

    if repo is None:
        return None
    try:
        return hub.issue(repo, number)
    except GithubError:
        return None


def _write_ticket_called(transcript: object) -> bool:
    """Whether the session's transcript holds a call of the `write-ticket` skill: a Skill tool
    use naming it (a plugin's `<plugin>:write-ticket` too), or the `/write-ticket` command. A
    transcript that is missing or unreadable holds none; a half-written line is skipped."""

    if not isinstance(transcript, str) or not transcript:
        return False
    try:
        with open(transcript, encoding="utf-8", errors="replace") as lines:
            for line in lines:
                if WRITE_TICKET in line and _calls_write_ticket(line):
                    return True
    except OSError:
        return False
    return False


def _calls_write_ticket(line: str) -> bool:
    try:
        record = json.loads(line)
    except ValueError:
        return False
    message = record.get("message") if isinstance(record, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if record.get("type") == "user" and isinstance(content, str):
        return f"<command-name>/{WRITE_TICKET}</command-name>" in content
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill":
            skill = (block.get("input") or {}).get("skill")
            if isinstance(skill, str) and (skill == WRITE_TICKET or skill.endswith(f":{WRITE_TICKET}")):
                return True
        if record.get("type") == "user" and isinstance(block, dict) and block.get("type") == "text":
            if f"<command-name>/{WRITE_TICKET}</command-name>" in str(block.get("text")):
                return True
    return False


def repo_of(folder: str | None) -> str | None:
    """`owner/name` of the folder's git `origin` on GitHub, or None."""

    from orch_it.testruns.runner import git_output

    origin = git_output(folder or ".", "remote", "get-url", "origin")
    found = _ORIGIN.search(origin) if origin else None
    return found.group(1) if found else None


def GhHub():  # noqa: N802 - the seam the tests replace
    from orch_it.tickets.hub import GhHub as Real

    return Real()


def main(text: str | None = None) -> int:
    """Read the PreToolUse payload (stdin), print a deny when refused; always exit 0."""

    try:
        payload = json.loads(sys.stdin.read() if text is None else text)
        if not isinstance(payload, dict):
            return 0
        refusal = judge(payload, hub=GhHub(), repo_of=repo_of)
    except Exception as e:  # noqa: BLE001 - a broken hook fails open, and says so
        print(f"tickets hook: internal error, failing open: {type(e).__name__}: {e}", file=sys.stderr)
        _record(None, None, error=type(e).__name__)
        return 0
    if refusal is None:  # no issue written: nothing to record
        return 0
    _record(payload, refusal)
    if refusal.codes:
        reason = "The tickets hook refused this command:\n" + refusal.message
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}))
    return 0


def _record(payload: dict | None, refusal: Refusal | None, *, error: str | None = None) -> None:
    """One event of what the hook did; a store that cannot be written is never the hook's error."""

    try:
        from orch_it.events.store import Store

        repo = (refusal.repo if refusal else None) or repo_of(None)
        if repo is None:
            return
        session = payload.get("session_id") if isinstance(payload, dict) else None
        envelope = {"repo": repo, "ticket": None, "role": "hook", "session": session if isinstance(session, str) else None,
                    "parent_session": None, "turn": None, "step": None, "run": None, "note": None}
        if error:
            event = {"kind": "error.raised", "detail": {"component": "tickets_hook", "error_code": error[:60],
                                                         "failed_open": True}}
        elif refusal.codes:
            first = refusal.codes[0]
            event = {"kind": "hook.ran", "detail": {"hook": HOOK, "action": "acted",
                                                     "reason": first if first in HOOK_CODES else "shape",
                                                     "rules": refusal.codes}}
        else:
            event = {"kind": "hook.ran", "detail": {"hook": HOOK, "action": "skipped", "reason": "passed",
                                                     "rules": []}}
        with Store.open().writer() as writer:
            writer.write(envelope | event)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    raise SystemExit(main())
