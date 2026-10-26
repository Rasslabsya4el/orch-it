"""Spawn (#399 decision 3): the one way a role's subagent is started. `spawn` fills the role's inputs
(the `inputs` of the roles settings) from one place, refuses with the input's name when one is missing,
writes the `subagent` step, and returns the prompt the caller passes to the Agent tool as it is.

The inputs:
- `issue`: the ticket `#N` of `repo`, fetched with `gh issue view` (body, labels and every comment) into
  `issue-N.md` of the spawn's folder; a ticket `gh` cannot read is `issue_unreadable`;
- `unit`: a unit id of ORCH's plan, a row of a Markdown table in the ticket whose first cell is the id;
  the row goes into the prompt, and an id with no row is `unit_not_found`;
- `diff`: a diff file, or a git range (`main...HEAD`) of the worktree that spawn writes to `diff.patch`;
  one that cannot be read is `diff_unreadable`, an empty one `diff_empty` (a review of nothing is no
  review: #380 counted two "0 findings" reviews made with no diff);
- `worktree`: the git work tree the subagent works in, default the one around the folder spawn runs
  in; a folder that is no work tree is `worktree_not_git`;
- `scratchpad`: the folder of the spawn's files (`<scratchpad>/<run>/`), default `spawn/` of orch-it's
  data folder; one inside a git work tree is `scratchpad_in_git_tree`, since those files must never
  reach a commit.

An input the role needs and the request lacks is `input_missing`. Each refusal names its input
(`SpawnRefused.input`). Every spawn of a role that has inputs writes `step.started` of step
`subagent` (run `spawn-<id>`, the role, the ticket, `prompt_has`: the inputs filled); a refused one
also writes its `step.ended` with outcome `refused` and `refused_input`. The prompt's line
`Spawn: <run>` lets the stop hook find the step again. The brain, with no inputs, is the owner's
chat: `role_not_spawned`.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orch_it import settings
from orch_it.events.ids import live_id
from orch_it.events.store import Store
from orch_it.roles import INPUTS, RoleSet

REFUSAL_CODES = ("role_unknown", "role_not_spawned", "input_missing", "issue_unreadable", "unit_not_found",
                 "diff_unreadable", "diff_empty", "worktree_not_git", "scratchpad_in_git_tree", "repo_unknown")
GIT_DIFF_TIMEOUT_S = 60
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ISSUE_FIELDS = "number,title,url,state,body,labels,comments"
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


class SpawnRefused(Exception):
    """A spawn that cannot start: `code` one of `REFUSAL_CODES`, `input` the input it is about
    (empty for the role's own refusals)."""

    def __init__(self, code: str, input_: str = "", detail: str = ""):
        if code not in REFUSAL_CODES:
            raise ValueError(f"not a spawn refusal code: {code}")
        self.code = code
        self.input = input_
        super().__init__(f"{code}: {input_}" + (f": {detail}" if detail else "") if input_ else code)


@dataclass(frozen=True)
class SpawnRequest:
    issue: int | None = None
    repo: str | None = None
    unit: str | None = None
    diff: str | None = None
    worktree: Path | None = None
    scratchpad: Path | None = None
    cwd: Path | None = None  # where spawn runs; the default worktree is the work tree around it

    def but(self, **changes) -> SpawnRequest:
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True)
class Spawned:
    run: str
    role: str
    prompt: str
    files: dict[str, Path]


def _work_tree_around(path: Path) -> Path | None:
    path = path.resolve()
    for folder in (path, *path.parents):
        if (folder / ".git").exists():
            return folder
    return None


def _issue_text(raw: dict) -> str:
    labels = ", ".join(label.get("name", "") for label in raw.get("labels") or [])
    parts = [f"# #{raw.get('number')} {raw.get('title', '')}", "",
             f"{raw.get('url', '')} ({raw.get('state', '')}; labels: {labels or 'none'})", "", raw.get("body") or ""]
    for comment in raw.get("comments") or []:
        author = (comment.get("author") or {}).get("login", "?")
        parts += ["", f"## Comment by {author}, {comment.get('createdAt', '')}", "", comment.get("body") or ""]
    return "\n".join(parts).rstrip() + "\n"


def _unit_row(text: str, unit: str) -> str | None:
    """The table row of the ticket whose first cell is `unit` (backticks and bold aside)."""

    for line in text.split("\n"):
        row = _TABLE_ROW.match(line)
        if row and row.group(1).split("|")[0].strip().strip("`*").strip() == unit:
            return line.strip()
    return None


def _diff_bytes(diff: str, worktree: Path | None) -> bytes:
    path = Path(diff)
    if path.is_file():
        try:
            return path.read_bytes()
        except OSError as e:
            raise SpawnRefused("diff_unreadable", "diff", str(e)) from None
    if worktree is None or not re.fullmatch(r"[\w./@^~{}-]+(?:\.\.\.?[\w./@^~{}-]+)?", diff) or diff.startswith("-"):
        raise SpawnRefused("diff_unreadable", "diff", f"{diff!r} is no file and no git range")
    try:
        done = subprocess.run(["git", "-C", str(worktree), "diff", diff, "--"], capture_output=True,
                              timeout=GIT_DIFF_TIMEOUT_S, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SpawnRefused("diff_unreadable", "diff", str(e)) from None
    if done.returncode != 0:
        raise SpawnRefused("diff_unreadable", "diff", done.stderr.decode("utf-8", "replace").strip()[:200])
    return done.stdout


def default_gh() -> Callable[[list[str]], str]:
    """The real `gh` runner (bounded by a timeout, no console window); the tests replace it."""

    from orch_it.testruns.github import run_gh

    return run_gh


def spawn(role: str, request: SpawnRequest, *, roles: RoleSet, store: Store,
          gh: Callable[[list[str]], str] | None = None) -> Spawned:
    """Fill `role`'s inputs from `request`, write the `subagent` step, and return the prompt; or
    raise `SpawnRefused` naming the input that is missing or cannot be used (the step is then
    written ended `refused`). `gh` runs one `gh` call (default: the real CLI)."""

    gh = gh or default_gh()
    if role not in roles.roles:
        raise SpawnRefused("role_unknown", detail=role)
    settings_of = roles.roles[role]
    if not settings_of.inputs:
        raise SpawnRefused("role_not_spawned", detail=f"{role} is a session, not a subagent")
    run = f"spawn-{live_id()}"
    needs = settings_of.inputs
    have: dict[str, str] = {}
    files: dict[str, Path] = {}
    worktree = request.worktree or _work_tree_around(request.cwd or Path.cwd())
    repo = request.repo
    unit_row = None

    def refuse(code: str, input_: str, detail: str = "") -> SpawnRefused:
        _record(store, run, role, settings_of, request, worktree, repo, have, None, refused=(code, input_))
        return SpawnRefused(code, input_, detail)

    if worktree is not None:
        if request.worktree is not None and _work_tree_around(request.worktree) != request.worktree.resolve():
            raise refuse("worktree_not_git", "worktree", str(request.worktree))
        have["worktree"] = str(worktree)
        if repo is None:
            from orch_it.tickets.hook import repo_of

            repo = repo_of(str(worktree))
    scratchpad = request.scratchpad or settings.data_folder() / "spawn"
    if _work_tree_around(scratchpad) is not None:
        raise refuse("scratchpad_in_git_tree", "scratchpad", str(scratchpad))
    folder = scratchpad / run
    have["scratchpad"] = str(folder)
    given = {"issue": request.issue, "unit": request.unit, "diff": request.diff, "worktree": worktree}
    for name in INPUTS:
        if name in needs and name in given and given[name] is None:
            raise refuse("input_missing", name)
    if repo is None:
        raise refuse("repo_unknown", "issue", "give --repo")
    folder.mkdir(parents=True, exist_ok=True)
    if "issue" in needs:
        try:
            raw = json.loads(gh(["issue", "view", str(request.issue), "--repo", repo, "--json", _ISSUE_FIELDS]))
            if not isinstance(raw, dict) or raw.get("number") != request.issue:
                raise ValueError("not the ticket asked for")
        except Exception as e:  # noqa: BLE001 - gh's error, a bad answer: the ticket cannot be read
            raise refuse("issue_unreadable", "issue", f"#{request.issue}: {e}"[:300]) from None
        files["issue"] = folder / f"issue-{request.issue}.md"
        text = _issue_text(raw)
        files["issue"].write_bytes(text.encode("utf-8"))
        have["issue"] = str(files["issue"])
        if "unit" in needs:
            unit_row = _unit_row(text, request.unit)
            if unit_row is None:
                raise refuse("unit_not_found", "unit", f"no row `{request.unit}` in the plan of #{request.issue}")
            have["unit"] = request.unit
    if "diff" in needs:
        try:
            content = _diff_bytes(request.diff, worktree)
        except SpawnRefused as e:
            raise refuse(e.code, "diff", str(e)) from None
        if not content.strip():
            raise refuse("diff_empty", "diff", request.diff)
        files["diff"] = folder / "diff.patch"
        files["diff"].write_bytes(content)
        have["diff"] = str(files["diff"])
    prompt = _prompt(role, run, request, needs, have, unit_row, raw.get("title", "") if "issue" in needs else "")
    _record(store, run, role, settings_of, request, worktree, repo, have, prompt)
    return Spawned(run, role, prompt, files)


_LABELS = {"issue": "Issue file", "unit": "Unit", "diff": "Diff file", "worktree": "Worktree",
           "scratchpad": "Scratchpad"}


def _prompt(role: str, run: str, request: SpawnRequest, needs: tuple[str, ...], have: dict[str, str],
            unit_row: str | None, title: str) -> str:
    lines = [f"You are the {role} of the orchestration layer: follow `.claude/agents/{role}.md`, step by step.",
             f"Spawn: {run}"]
    if "issue" in needs:
        lines.append(f"Ticket: #{request.issue} {title}".rstrip())
    for name in needs:
        value = have[name]
        if name == "unit":
            value = f"{value}, the plan's row: {unit_row}"
        lines.append(f"{_LABELS[name]}: {value}")
    lines.append("Every file above was written at spawn for you: read each one whole before your first step.")
    return "\n".join(lines) + "\n"


def _record(store: Store, run: str, role: str, settings_of, request: SpawnRequest, worktree: Path | None,
            repo: str | None, have: dict[str, str], prompt: str | None, refused: tuple[str, str] | None = None) -> None:
    """The `subagent` step of a spawn; a spawn whose repo is unknown has no envelope and writes nothing."""

    if repo is None:
        return
    envelope = {"repo": repo, "ticket": request.issue if request.issue and request.issue > 0 else None,
                "role": role, "session": None, "parent_session": None, "turn": None, "step": "subagent", "run": run,
                "note": None}
    started = {"model": settings_of.model, "effort": settings_of.effort, "agent_file": f".claude/agents/{role}.md",
               "title": f"{role} #{request.issue}" + (f" unit {request.unit}" if request.unit else ""),
               "launched_by": "session", "unit": request.unit, "worktree": str(worktree) if worktree else None,
               "prompt_has": [name for name in INPUTS if name in have], "prompt_chars": len(prompt or "")}
    with store.writer() as writer:
        writer.write(envelope | {"kind": "step.started", "detail": started})
        if refused:
            writer.write(envelope | {"kind": "step.ended", "detail": {
                "outcome": "refused", "refused_code": refused[0], "refused_input": refused[1]}})
