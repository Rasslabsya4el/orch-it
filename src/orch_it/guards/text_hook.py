"""The value check on text for another reader (#415 unit 4), judged on each call by its input, not by its tool name.

`decide` is the PreToolUse hook (`python -m orch_it.guards.text_hook`, wired in `.claude/settings.json` with a
catch-all matcher). A call whose input carries text for another reader (a prompt, a message, a file's content, an
edit's new text, a description, a body; found in any nesting) is checked for the eleven kinds of the coding
standard: the eight value kinds of `guards.values` and the three pattern kinds of `guards.kinds`.

A write of a file inside the repo leaves the machine, so its text gets no per-machine value at all; a write of a
source file (`SOURCE_SUFFIXES`, outside `docs/`) gets the value kinds only, its pattern kinds being legitimate code. Any other call
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

# every string of the call's input is text for another reader, except the keys that name a place, a kind of agent or
# what a search looks for (Grep's and Glob's pattern and glob, an Edit's old_string: the text it finds, not writes),
# never text for a reader; a tool no list names is judged too
STRUCTURAL_KEYS = ("file_path", "path", "subagent_type", "model", "pattern", "glob", "notebook_path", "cell_id",
                   "edit_mode", "old_string")
SHELL_TOOLS = ("Bash", "PowerShell")  # their commands and descriptions are other guards' business, not judged here
FILE_KEY = "file_path"  # a write of a file: judged only when the file is inside the repo
# a source file (a code folder's) gets the value kinds only: its one-OS words and call forms are legitimate code
# (guards.kinds flags them there); a `docs/` file or a suffix no list names keeps all the kinds
SOURCE_SUFFIXES = (".py", ".sh", ".ps1", ".js", ".ts", ".yml", ".yaml", ".toml")

_SESSION_ID = r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
_TOKEN = r"[^\s\"'`<>|,()\[\]{}]*"  # a path or a word of a text: up to a space, quote, comma, bracket or pipe


def _scratchpad_pattern(repo: str) -> re.Pattern:
    """A session's scratchpad of this repo: `<any folder>/<the project's slug>/<session id>/scratchpad`. The slug is
    the repo folder path with every non-alphanumeric character turned into '-', computed from `repo`; the folder
    before it is any. Another project's slug is no match."""

    slug = re.sub(r"[^A-Za-z0-9]", "-", repo)
    return re.compile(rf"{_TOKEN}[\\/]+{re.escape(slug)}[\\/]+{_SESSION_ID}[\\/]+scratchpad(?![A-Za-z0-9_-]){_TOKEN}",
                      re.IGNORECASE)


@dataclass(frozen=True)
class Context:
    """The setup a check judges with: the values read (`Values`), the names of the repo's workflow checks, the
    repo's roots (its worktrees and its common git folder, each as its real path) and the repo's main checkout
    (`repo`, its real path; `""` outside a repo). The main checkout gives the project's slug and its worktree folder."""

    values: Values
    job_names: frozenset[str]
    roots: tuple[str, ...]
    repo: str = ""


@dataclass(frozen=True)
class Hit:
    """One finding: its `kind` (one of the eleven) and the `line` of the text it is on (1-based)."""

    kind: str
    line: int


GH_SECONDS = 5  # the bound of the hook's `gh api user`: a login that is not read in time is a value not checked


def _bounded_run(cmd: list[str]) -> str:
    """The output of `cmd` as `values.default_run` gives it, with `gh` bounded to `GH_SECONDS` in this hook (the
    default bound for every other command)."""

    from orch_it.guards.values import COMMAND_SECONDS, default_run

    return default_run(cmd, timeout=GH_SECONDS if cmd[:1] == ["gh"] else COMMAND_SECONDS)


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
    roots = repo_roots(work)
    # git lists the main checkout first, before the linked worktrees and the common folder
    return Context(values=read_values(run=_bounded_run), job_names=read_job_names(str(Path(work) / ".github" / "workflows")),
                   roots=roots, repo=roots[0] if roots else "")


def find_text(text: str, *, stays: bool, context: Context, path: str | None = None,
              patterns: bool = True) -> list[Hit]:
    """The hits of `text`: each value of the setup in any spelling (`guards.values.find_in`) and, when `patterns`, each
    pattern kind. The pattern kinds are judged as a record of the file `path` when the text is a file's
    (`guards.kinds.find_in_record`), and as ticket text otherwise (`guards.kinds.find_in_ticket_text`). For text that
    stays on this machine, the run-time locations are masked first, so a token that is a session's scratchpad, a
    worktree, or a path inside a root is no hit."""

    from orch_it.guards.kinds import find_in_record, find_in_ticket_text
    from orch_it.guards.values import find_in

    checked = _masked(text, context.roots, context.repo) if stays else text
    found = [Hit(finding.kind, finding.line) for finding in find_in(checked, context.values)]
    if patterns:
        if path is None:
            pattern = find_in_ticket_text(checked, context.job_names)
        else:
            pattern = find_in_record(path, checked, context.job_names)
        found += [Hit(finding.kind, finding.line) for finding in pattern]
    return sorted(set(found), key=lambda hit: (hit.line, hit.kind))


def judge_call(payload: dict, context: Callable[[str | None], Context]) -> tuple[list[Hit], str]:
    """The hits of the call's text and the not-checked line of the setup it was judged with (`""` when none).

    The text is every string leaf of the input but the structural keys, whatever the tool's name; a shell call is not
    judged. A call with no text reads no setup. A call with a `file_path` is judged only when that file is inside the
    repo, as text that leaves the machine; any other call is judged as text that stays on this machine.
    `context(folder)` gives the setup."""

    tool_input = payload.get("tool_input")
    if payload.get("tool_name") in SHELL_TOOLS:
        return [], ""
    texts = list(_texts(tool_input)) if isinstance(tool_input, dict) else []
    if not texts:
        return [], ""
    folder = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    setup = context(folder)
    file = tool_input.get(FILE_KEY)
    if isinstance(file, str):
        root = _root_of(file, folder, setup.roots)
        if root is None:
            return [], ""
        patterns = _patterns_judged(file, folder, root)
        hits = [hit for text in texts
                for hit in find_text(text, stays=False, context=setup, path=file, patterns=patterns)]
    else:
        hits = [hit for text in texts for hit in find_text(text, stays=True, context=setup)]
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


def _texts(node: object, key: str | None = None) -> Iterator[str]:
    """Every non-empty string leaf of `node`, at any depth (a `MultiEdit`'s `edits` included), except a string under a
    structural key; a list's strings take the key of the list."""

    if isinstance(node, str):
        if node and key not in STRUCTURAL_KEYS:
            yield node
    elif isinstance(node, dict):
        for name, value in node.items():
            yield from _texts(value, name)
    elif isinstance(node, list):
        for item in node:
            yield from _texts(item, key)


def _masked(text: str, roots: tuple[str, ...], repo: str) -> str:
    """`text` with each run-time location blanked to spaces, the lines and their numbers kept: this project's
    scratchpads, the worktree folder of this repo, and the paths inside a root."""

    patterns = [_scratchpad_pattern(repo), _root_pattern(f"{repo}/.claude/worktrees")] if repo else []
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


def _absolute(file: str, folder: str | None) -> str:
    return os.path.normcase(os.path.normpath(file if folder is None else os.path.join(folder, file)))


def _root_of(file: str, folder: str | None, roots: tuple[str, ...]) -> str | None:
    """The root that holds `file` (relative to `folder`, or absolute), the longest one when roots nest; None when no
    root is a root of it or lies above it."""

    path = _absolute(file, folder)
    best: str | None = None
    for root in roots:
        base = os.path.normcase(os.path.normpath(root))
        if path == base or path.startswith(base.rstrip(os.sep) + os.sep):
            if best is None or len(base) > len(os.path.normcase(os.path.normpath(best))):
                best = root
    return best


def _patterns_judged(file: str, folder: str | None, root: str) -> bool:
    """True when a file write gets the pattern kinds as well as the value kinds: every file except a source file (a
    suffix of `SOURCE_SUFFIXES`) outside the root's `docs/` folder."""

    suffix = os.path.splitext(file)[1].lower()
    return not (suffix in SOURCE_SUFFIXES and not _in_docs(file, folder, root))


def _in_docs(file: str, folder: str | None, root: str) -> bool:
    """True when `file` lies under the `docs/` folder of `root`."""

    base = os.path.normcase(os.path.normpath(root)).rstrip(os.sep)
    rest = _absolute(file, folder)[len(base):].lstrip(os.sep)
    return rest.split(os.sep, 1)[0] == "docs"


if __name__ == "__main__":
    raise SystemExit(main())
