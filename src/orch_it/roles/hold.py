"""The report-line hold (#399 decision 6): a Claude Code SubagentStop hook,
`python -m orch_it hook subagent-stop`, that reads the stopping subagent's own transcript
(`agent_transcript_path`; the parent's transcript is never read) and holds its stop once when the
final report breaks a rule of its role's `report` in the roles settings:

- a line `tdd:`, `diagnosis:`, `code-review:`, `research:`, `writing-for-agents:`,
  `domain-modeling:` or `codebase-design:` that claims a call (any value but a no-claim form such
  as `skipped (why)`, `n/a`, `none`, `not a defect`) with no Skill tool use of that skill in the
  subagent's transcript (with or without a `plugin:` prefix);
- a unit that edits a code file (Edit, Write, MultiEdit or NotebookEdit of a file that is no
  Markdown and not under a scratch or temp folder) with no `tdd:` line;
- a line the role `owes` (researcher-how's `research:`) that is missing;
- a report of more non-empty lines than the role's `cap_lines`;
- a context past the role's `context_line` (the largest prompt of any of its turns: input, cache
  read and cache write tokens) with no `table: <path>` line naming a file that exists: survey's
  100k hand-back, its table written as it goes.

A role with no `report` in the settings (ORCH, the brain, any other agent type) is never read. The
final report is `last_assistant_message`, else the last assistant text of the transcript; lines in a
fenced block or a quote are no report lines. A stop already held once (`stop_hook_active`) passes,
so a subagent is held at most once and never loops. All problems of a stop go into one reason.

Each judged stop writes one `report_line.checked` per line it judged (`claimed`, `verified`,
`accepted`; `first_edit_path` for `tdd`; for `report_cap` the line count and the cap), a
`context.crossed` when the context line was passed, and, when the stop is let through and the
prompt carried `Spawn: <run>`, the end of that spawn's `subagent` step (outcome `passed`, or
`failed` when a held problem was never fixed). A transcript that cannot be read, a payload that is
no object, or any error of the hook itself lets the stop through (`error.raised`, `failed_open`):
a broken hook must not stop a session.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from orch_it.roles import RoleSet

SKILL_OF_LINE = {"tdd": "tdd", "diagnosis": "diagnosing-bugs", "code-review": "code-review", "research": "research",
                 "writing-for-agents": "writing-for-agents", "domain-modeling": "domain-modeling",
                 "codebase-design": "codebase-design"}
CODE_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
REASON_HEADER = "The stop is held once; fix the final report, then end again:"
_LINE = re.compile(r"(?im)^\W*(" + "|".join(map(re.escape, [*SKILL_OF_LINE, "table"])) + r")\s*:[ \t]*(.*)$")
_NO_CLAIM = re.compile(r"^(skipped|n/a|na|none|not a defect|no defect|not applicable|not run|not needed|not called"
                       r"|not used|not invoked)\b", re.IGNORECASE)
_SPAWN = re.compile(r"^Spawn:\s*(spawn-[\w-]+)\s*$", re.MULTILINE)
_FENCES = ("```", "~~~")


@dataclass
class Verdict:
    role: str
    run: str | None = None
    problems: list[tuple[str, str]] = field(default_factory=list)  # (label, the sentence of the reason)
    checks: list[dict] = field(default_factory=list)  # the detail of each `report_line.checked`
    owed: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    context_tokens: int = 0
    crossed: bool = False
    table_named: bool = False

    def reason(self) -> str:
        return "\n".join([REASON_HEADER] + [f"- {sentence}" for _, sentence in self.problems])


def _records(path: str) -> list[dict]:
    """The transcript's JSON objects; a half-written or broken line is skipped. A file that cannot
    be read raises OSError."""

    records = []
    with open(path, encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def _blocks(record: dict) -> list[dict]:
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _tool_uses(records: list[dict]) -> list[dict]:
    return [b for r in records if r.get("type") == "assistant" for b in _blocks(r) if b.get("type") == "tool_use"]


def _skills(records: list[dict]) -> set[str]:
    names = set()
    for block in _tool_uses(records):
        given = block.get("input")
        skill = given.get("skill") if block.get("name") == "Skill" and isinstance(given, dict) else None
        if isinstance(skill, str) and skill.strip():
            names.add(skill.strip().rsplit(":", 1)[-1].strip().lower())
    return names


def _code_edits(records: list[dict]) -> list[str]:
    paths = []
    for block in _tool_uses(records):
        given = block.get("input")
        path = given.get("file_path") or given.get("notebook_path") if isinstance(given, dict) else None
        if block.get("name") in CODE_EDIT_TOOLS and isinstance(path, str) and _is_code(path):
            paths.append(path)
    return paths


def _is_code(path: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    if lowered.endswith((".md", ".markdown", ".txt")):
        return False
    return not any("scratch" in part or part in ("tmp", "temp") for part in lowered.split("/"))


def _final(payload: dict, records: list[dict]) -> str:
    message = payload.get("last_assistant_message")
    if isinstance(message, str) and message.strip():
        return message
    last = ""
    for record in records:
        if record.get("type") == "assistant":
            last = next((b["text"] for b in reversed(_blocks(record))
                         if b.get("type") == "text" and isinstance(b.get("text"), str)), last)
    return last


def _report_lines(text: str) -> list[str]:
    """The lines that can be report lines: not inside a fenced block, not a quote."""

    kept, fence = [], None
    for line in text.splitlines():
        marker = line.lstrip()[:3]
        if fence is None and marker in _FENCES:
            fence = marker
        elif fence is not None:
            fence = None if marker == fence else fence
        elif not line.lstrip().startswith(">"):
            kept.append(line)
    return kept


def _context(records: list[dict]) -> int:
    """The largest prompt of any turn: input, cache read and cache write tokens."""

    largest = 0
    for record in records:
        message = record.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(usage, dict):
            parts = [usage.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
            largest = max(largest, sum(p for p in parts if isinstance(p, int) and p > 0))
    return largest


def _spawn_run(records: list[dict]) -> str | None:
    first = next((r for r in records if r.get("type") == "user"), None)
    text = "\n".join(str(b.get("text", "")) for b in _blocks(first) if b.get("type") == "text") if first else ""
    found = _SPAWN.search(text)
    return found.group(1) if found else None


def judge(payload: dict, roles: RoleSet, *, work_tree: str | Path | None = None) -> Verdict | None:
    """The verdict on a SubagentStop payload, or None when there is nothing to judge (a role with
    no `report`, a transcript that cannot be read)."""

    agent = payload.get("agent_type")
    role = agent.rsplit(":", 1)[-1] if isinstance(agent, str) else None
    settings_of = roles.roles.get(role) if role else None
    path = payload.get("agent_transcript_path")
    if settings_of is None or settings_of.report is None or not isinstance(path, str) or not path:
        return None
    try:
        records = _records(path)
    except OSError:
        return None
    rule = settings_of.report
    final = _final(payload, records)
    lines = _report_lines(final)
    called = _skills(records)
    edits = _code_edits(records)
    verdict = Verdict(role, _spawn_run(records), context_tokens=_context(records))
    claimed: dict[str, str] = {}
    for line in lines:
        found = _LINE.match(line)
        if found:
            claimed.setdefault(found.group(1).lower(), re.sub(r"^\W+", "", found.group(2).strip()))
    verdict.present = [label for label in claimed if label != "table"]
    for label, value in claimed.items():
        if label == "table" or _NO_CLAIM.match(value):
            continue
        skill = SKILL_OF_LINE[label]
        backed = skill in called
        verdict.checks.append({"label": label, "claimed": True, "verified": backed, "accepted": backed})
        if not backed:
            verdict.problems.append((label, f"`{label}:` claims the skill `{skill}`, and this subagent never called it."
                                            f" Call the skill `{skill}` now, or write the line as"
                                            f" `{label}: skipped (why)`."))
    if edits:
        verdict.owed.append("tdd")
        if "tdd" not in claimed:
            verdict.checks.append({"label": "tdd", "claimed": False, "verified": "tdd" in called, "accepted": False,
                                   "first_edit_path": edits[0]})
            verdict.problems.append(("tdd", f"this unit edits code (`{edits[0]}`) and the report has no `tdd:` line."
                                            " Call the skill `tdd` and report it, or end with `tdd: skipped (why)`."))
    for label in rule.owes:
        verdict.owed.append(label)
        if label not in claimed:
            verdict.checks.append({"label": label, "claimed": False, "verified": SKILL_OF_LINE[label] in called,
                                   "accepted": False})
            verdict.problems.append((label, f"your role owes the line `{label}:` and the report has none."))
    count = sum(1 for line in final.splitlines() if line.strip())
    if rule.cap_lines is not None and count > rule.cap_lines:
        verdict.checks.append({"label": "report_cap", "claimed": count, "verified": rule.cap_lines, "accepted": False})
        verdict.problems.append(("report_cap", f"the report has {count} lines; your role's cap is {rule.cap_lines}."
                                               " Keep the lines that carry a fact and its source."))
    if rule.context_line is not None and verdict.context_tokens > rule.context_line:
        verdict.crossed = True
        table = claimed.get("table", "").strip().strip("`")
        base = Path(work_tree or payload.get("cwd") or ".")
        verdict.table_named = bool(table) and (Path(table) if Path(table).is_absolute() else base / table).is_file()
        verdict.checks.append({"label": "table", "claimed": bool(table), "verified": verdict.table_named,
                               "accepted": verdict.table_named})
        if not verdict.table_named:
            verdict.problems.append(("table", f"your context passed {rule.context_line} tokens: hand back now with the"
                                              " line `table: <path>` naming the table file written so far."))
    return verdict


def record(verdict: Verdict, payload: dict, *, store, repo: str, let_through: bool) -> None:
    """The events of a judged stop."""

    session = payload.get("agent_id") if isinstance(payload.get("agent_id"), str) else None
    parent = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
    envelope = {"repo": repo, "ticket": None, "role": verdict.role, "session": session, "parent_session": parent,
                "turn": None, "step": None, "run": verdict.run, "note": None}
    with store.writer() as writer:
        for check in verdict.checks:
            writer.write(envelope | {"kind": "report_line.checked", "detail": check})
        if verdict.crossed:
            writer.write(envelope | {"kind": "context.crossed", "detail": {
                "threshold": "context_line", "tokens": verdict.context_tokens,
                "action_after": "handed_off" if verdict.table_named else "told"}})
        if let_through and verdict.run:
            writer.write(envelope | {"step": "subagent", "kind": "step.ended", "detail": {
                "outcome": "failed" if verdict.problems else "passed", "report_lines_owed": verdict.owed,
                "report_lines_present": verdict.present}})


def run_subagent_stop(raw: str, *, store=None, repo: str | None = None) -> tuple[str, int]:
    """The hook: (stdout, exit code). Prints a block decision when the stop is held; always exits 0."""

    payload = None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return "", 0
        verdict = judge(payload, RoleSet.load())
        if verdict is None:
            return "", 0
        held = bool(verdict.problems) and not payload.get("stop_hook_active")
        _record_safely(verdict, payload, store, repo, let_through=not held)
        if held:
            return json.dumps({"decision": "block", "reason": verdict.reason()}), 0
        return "", 0
    except Exception as e:  # noqa: BLE001 - a broken hook lets the stop through, and says so
        _error(payload, store, repo, type(e).__name__)
        return "", 0


def _repo_and_store(payload: dict | None, store, repo):
    if repo is None:
        from orch_it.tickets.hook import repo_of

        cwd = payload.get("cwd") if isinstance(payload, dict) and isinstance(payload.get("cwd"), str) else None
        repo = repo_of(cwd)
    if store is None and repo is not None:
        from orch_it.events.store import Store

        store = Store.open()
    return store, repo


def _record_safely(verdict: Verdict, payload: dict, store, repo, *, let_through: bool) -> None:
    try:
        store, repo = _repo_and_store(payload, store, repo)
        if store is not None and repo is not None:
            record(verdict, payload, store=store, repo=repo, let_through=let_through)
    except Exception:  # noqa: BLE001 - a store that cannot be written is never the stop's problem
        pass


def _error(payload: dict | None, store, repo, code: str) -> None:
    try:
        store, repo = _repo_and_store(payload, store, repo)
        if store is None or repo is None:
            return
        with store.writer() as writer:
            writer.write({"kind": "error.raised", "repo": repo, "ticket": None, "role": "hook", "session": None,
                          "parent_session": None, "turn": None, "step": None, "run": None, "note": None,
                          "detail": {"component": "subagent_stop", "error_code": code[:60], "failed_open": True}})
    except Exception:  # noqa: BLE001
        pass
