"""The value check on text for another reader (#415 unit 4), judged on each call by its input, not by its tool name.

`decide` is the PreToolUse hook (`python -m orch_it.guards.text_hook`, wired in `.claude/settings.json` with a
catch-all matcher). A call whose input carries text for another reader (a prompt, a message, a file's content, an
edit's new text, a description, a body; found in any nesting) is checked for the eleven kinds of the coding
standard: the eight value kinds of `guards.values` and the three pattern kinds of `guards.kinds`.

A write of a file inside the repo leaves the machine, so its text gets no per-machine value at all. Any other call
stays on this machine, so its text may name the run-time locations the harness gives a session (a session's
scratchpad, a worktree) and paths inside the repo. A check that cannot run refuses the call: every exception, an
import failure included, is a refusal naming the install step.

The modules of the check are imported inside the functions that use them, so an import failure is raised there and
caught by `decide`; `main` stays a plain entry point that always exits 0 and prints the deny as JSON.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orch_it.guards.values import Values

# the keys whose value is text for another reader, found in any nesting of the call's input
TEXT_KEYS = ("prompt", "message", "content", "new_string", "description", "body")
FILE_KEY = "file_path"  # a write of a file: judged only when the file is inside the repo

_SESSION_ID = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
_TOKEN = r"[^\s\"'`<>|]*"  # a path or a word of a text: up to a space, a quote, a bracket or a pipe
# the run-time locations the harness gives a session; masked in the text that stays on this machine only
_SCRATCHPAD = re.compile(rf"{_TOKEN}{_SESSION_ID}[\\/]+scratchpad(?![A-Za-z0-9_-]){_TOKEN}")
_WORKTREE = re.compile(rf"{_TOKEN}[\\/]\.claude[\\/]+worktrees[\\/]+{_TOKEN}")


@dataclass(frozen=True)
class Context:
    """The setup a check judges with: the values read (`Values`), the names of the repo's workflow checks, and the
    repo's roots (its worktrees and its common git folder, each as its real path)."""

    values: Values
    job_names: frozenset[str]
    roots: tuple[str, ...]


@dataclass(frozen=True)
class Hit:
    """One finding: its `kind` (one of the eleven) and the `line` of the text it is on (1-based)."""

    kind: str
    line: int


def repo_roots(folder: str | None) -> tuple[str, ...]:
    """The roots of the repo around `folder`: its worktrees and its common git folder; `()` outside a repo."""

    from orch_it.guards.paths import protected_roots

    return protected_roots(folder or os.getcwd(), windows=sys.platform == "win32")


def read_context(folder: str | None) -> Context:
    """The check's setup as `folder` sees it: this machine's values, the repo's workflow check names and its roots.
    Any failure raises (a module that cannot be imported included); `decide` refuses the call then."""

    from orch_it.guards.kinds import read_job_names
    from orch_it.guards.values import read_values

    work = folder or os.getcwd()
    return Context(values=read_values(), job_names=read_job_names(str(Path(work) / ".github" / "workflows")),
                   roots=repo_roots(work))


def find_text(text: str, *, stays: bool, context: Context) -> list[Hit]:
    """The hits of `text`: each value of the setup in any spelling (`guards.values.find_in`) and each pattern kind
    (`guards.kinds.find_in_ticket_text`). For text that stays on this machine, the run-time locations are masked
    first, so a token that is a session's scratchpad, a worktree, or a path inside a root is no hit."""

    from orch_it.guards.kinds import find_in_ticket_text
    from orch_it.guards.values import find_in

    checked = _masked(text, context.roots) if stays else text
    found = [Hit(finding.kind, finding.line) for finding in find_in(checked, context.values)]
    found += [Hit(finding.kind, finding.line) for finding in find_in_ticket_text(checked, context.job_names)]
    return sorted(found, key=lambda hit: (hit.line, hit.kind))


def judge_call(payload: dict, context: Callable[[str | None], Context]) -> tuple[list[Hit], str]:
    """The hits of the call's text and the not-checked line of the setup it was judged with (`""` when none).

    The text is found by the input's keys, so a tool no list names is judged too; a call with no text reads no
    setup. A call with a `file_path` is judged only when that file is inside the repo, as text that leaves the
    machine; any other call is judged as text that stays on this machine. `context(folder)` gives the setup."""

    tool_input = payload.get("tool_input")
    texts = list(_texts(tool_input)) if isinstance(tool_input, dict) else []
    if not texts:
        return [], ""
    folder = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    setup = context(folder)
    file = tool_input.get(FILE_KEY)
    stays = not isinstance(file, str)
    if not stays and not _inside(file, folder, setup.roots):
        return [], ""
    hits = [hit for text in texts for hit in find_text(text, stays=stays, context=setup)]
    return hits, setup.values.render_not_checked()


def decide(raw: str, *, context: Callable[[str | None], Context] | None = None) -> tuple[str | None, str]:
    """The verdict on one PreToolUse payload (JSON text): `(reason, notice)`; `reason` is None when the call passes,
    and `notice` is the not-checked line of the setup (`""` when every kind was checked). A check that cannot run
    refuses the call with the install step named: any exception, an import failure included, never a pass."""

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None, ""
        hits, notice = judge_call(payload, read_context if context is None else context)
        if not hits:
            return None, notice
        return _refusal(hits), notice
    except Exception as e:  # noqa: BLE001 - a check that cannot run refuses the call; it never passes
        return _unreachable(e), ""


def main(text: str | None = None) -> int:
    """Read the PreToolUse payload (stdin, or `text`) and print a deny as JSON when the call is refused. Exits 0
    whatever happens: the deny is the JSON on stdout, not the exit code. Any failure of the read, the check or the
    print is a deny naming the install step."""

    try:
        raw = sys.stdin.read() if text is None else text
        reason, notice = decide(raw)
        if reason is None:
            return 0
        if notice:
            reason = f"{reason}\n{notice}"
        print(_deny(reason))
    except Exception as e:  # noqa: BLE001 - whatever stops the hook, the call is refused, never passed
        print(_deny(_unreachable(e)))
    return 0


def _deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": reason}})


def _unreachable(error: BaseException) -> str:
    """The refusal of a check that cannot run: `text_check_unreachable` in the human language, or the same sentence
    in English when the language module itself cannot be used."""

    detail = f"{type(error).__name__}: {error}"
    python = sys.executable
    try:
        from orch_it import language

        return language.text_or_refusal("text_check_unreachable", error=detail, python=python)
    except Exception:  # noqa: BLE001 - the language module is part of what may be broken
        return (f"the text check cannot run ({detail}), so the call is refused, not passed; install orch-it into "
                f"the Python the hooks run with `{python} -m pip install -e <orch-it checkout>`, then send the call "
                "again")


def _refusal(hits: list[Hit]) -> str:
    from orch_it import language

    findings = "\n".join(language.text_or_refusal("text_finding", line=hit.line, kind=hit.kind) for hit in hits)
    rule = language.text_or_refusal("text_rule")
    return (language.text_or_refusal("text_refused", rule=rule, findings=findings)
            + "\n" + language.text_or_refusal("text_refused_way"))


def _texts(node: object) -> Iterator[str]:
    """Every non-empty string under a text key of `node`, at any depth (a `MultiEdit`'s `edits` included)."""

    if isinstance(node, dict):
        for key, value in node.items():
            if key in TEXT_KEYS and isinstance(value, str):
                if value:
                    yield value
            else:
                yield from _texts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _texts(item)


def _masked(text: str, roots: tuple[str, ...]) -> str:
    """`text` with each run-time location blanked to spaces, the lines and their numbers kept."""

    patterns = [_SCRATCHPAD, _WORKTREE]
    patterns += [pattern for pattern in map(_root_pattern, roots) if pattern is not None]
    for pattern in patterns:
        text = pattern.sub(lambda match: " " * len(match.group()), text)
    return text


def _root_pattern(root: str) -> re.Pattern | None:
    """The regex of a root in either slash spelling, with the token that follows it; None for an empty root (which
    would match every token). The root must end at a separator: `proj-y` is not `proj-y-old`."""

    parts = [part for part in re.split(r"[\\/]+", root) if part]
    if not parts:
        return None
    body = r"[\\/]+".join(re.escape(part) for part in parts)
    lead = r"[\\/]+" if root[:1] in ("/", "\\") else ""
    return re.compile(rf"(?<![A-Za-z0-9_]){lead}{body}(?![A-Za-z0-9_-]){_TOKEN}", re.IGNORECASE)


def _inside(file: str, folder: str | None, roots: tuple[str, ...]) -> bool:
    """True when `file` (relative to `folder`, or absolute) is a root or lies under one."""

    path = os.path.normcase(os.path.normpath(file if folder is None else os.path.join(folder, file)))
    for root in roots:
        base = os.path.normcase(os.path.normpath(root))
        if path == base or path.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
