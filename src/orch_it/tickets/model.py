"""The ticket model (#398 decision 1): one parser reads a ticket body into its fields.

A body is read in this order: at the top only blank lines, `Parked:` lines and HTML comments,
then the heading `## For the human` with the five fields `- **What:**`, `- **Goal:**`,
`- **Plan:**`, `- **Result:**`, `- **Undo:**` (a field runs on over its continuation lines),
then the heading `## For the agent` and everything after it. In the agent block, outside fenced
code, the parser reads the first table whose header names a `check` and a `pass` column (the
acceptance table: its first cell the row id, `<id> after merge` marking a row checked only after
the merge) and the lines `Outside case:`, `Class: none (<why>)`, `Same object:`,
`Not in this ticket:` (or a `### Not in this ticket` section), `Weight:` and `Adds:` (the roles
and steps the ticket adds, `Adds: step claim, role launcher`).

What a person writes on github.com is read too: CRLF line ends, a heading in another letter case
or closed with `#`s or a colon, a bullet `*` or `+`, the colon outside the bold (`**Plan**:`), a
line field in bold or as a bullet (`- **Weight:** 3`).

`parse` never raises: any text, empty, half-written or huge, gives a `Ticket` whose missing
parts are `None` or empty, and judging them is the shape rules' work (`shape.violations`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

HUMAN_HEADING = "## For the human"
AGENT_HEADING = "## For the agent"
HUMAN_FIELDS = ("What", "Goal", "Plan", "Result", "Undo")
PARK_PREFIX = "Parked:"

READY_LABEL = "ready-for-agent"
PARKED_LABEL = "parked"
PRIORITY_LABEL = "priority"

_FENCE = re.compile(r"\s{0,3}(```|~~~)")
_BULLET = re.compile(r"[-*+]\s+")
_BOLD_NAME = re.compile(r"\*\*([^*\n]+?)(?::\*\*|\*\*:)\s*")  # `**Name:**` or `**Name**:`
_AFTER_MERGE = re.compile(r"(.*?)[\s,;]*\(?after merge\)?", re.IGNORECASE)
_NOT_IN_HEADING = re.compile(r"#{2,6}\s*Not in this ticket\s*:?\s*#*", re.IGNORECASE)
_NONE = re.compile(r"none\b\s*(?:\((?P<paren>.*)\)\s*\.?|[-–—:,;]\s*(?P<after>.*)|\.?)",
                   re.IGNORECASE | re.DOTALL)
_WEIGHT = re.compile(r"(\d{1,3})(?:\s.*)?", re.DOTALL)


@dataclass(frozen=True)
class AcceptanceRow:
    id: str
    check: str
    passes: str  # the `pass` cell
    after_merge: bool


@dataclass(frozen=True)
class HumanField:
    name: str
    text: str


@dataclass(frozen=True)
class Ticket:
    title: str | None
    has_human_block: bool
    human_block_on_top: bool  # above its heading only blank lines, `Parked:` lines and comments
    human_fields: tuple[HumanField, ...]  # every field line found, in body order, repeats kept
    agent: str | None  # the agent block's text, None without its heading
    acceptance: tuple[AcceptanceRow, ...] | None  # None without an acceptance table
    outside_case: str | None
    class_none: str | None  # the why of `Class: none (<why>)`
    same_object: str | None
    not_in_ticket: str | None
    weight_text: str | None  # what follows `Weight:`
    adds: tuple[str, ...]  # the items of `Adds:`, e.g. `step claim`, `role launcher`
    park_lines: tuple[str, ...]  # every `Parked:` line outside fenced code, wherever it stands
    labels: frozenset[str]

    @property
    def human(self) -> dict[str, str]:
        """The five fields by name, the first of each found, in body order."""

        found: dict[str, str] = {}
        for field in self.human_fields:
            found.setdefault(field.name, field.text)
        return found

    @property
    def weight(self) -> int | None:
        """`Weight: <n>` as a whole number, words after a space ignored (`7 (two parts)`);
        None when it is no whole number (`7abc`, `5-6`, `3.5`)."""

        found = _WEIGHT.fullmatch(self.weight_text or "")
        return int(found.group(1)) if found else None

    @property
    def ready(self) -> bool:
        return READY_LABEL in self.labels

    @property
    def parked(self) -> bool:
        return PARKED_LABEL in self.labels

    @property
    def priority(self) -> bool:
        return PRIORITY_LABEL in self.labels


def parse(body: str | None, *, title: str | None = None, labels: Iterable[str] = ()) -> Ticket:
    """The ticket written in `body`, with its `title` and `labels` as GitHub holds them."""

    text = body if isinstance(body, str) else ""
    lines = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    plain = _outside_fences(lines)  # (index, line) of every line outside fenced code
    human_at = _heading_at(plain, HUMAN_HEADING, 0)
    agent_at = _heading_at(plain, AGENT_HEADING, 0 if human_at is None else human_at + 1)

    human_fields: tuple[HumanField, ...] = ()
    if human_at is not None:
        end = agent_at if agent_at is not None else len(lines)
        human_fields = _human_fields([line for i, line in plain if human_at < i < end])

    agent_lines = [line for i, line in plain if agent_at is not None and i > agent_at]
    singles = _line_fields(agent_lines)
    return Ticket(
        title=title if isinstance(title, str) else None,
        has_human_block=human_at is not None,
        human_block_on_top=human_at is not None and _only_park_lines_above(lines[:human_at]),
        human_fields=human_fields,
        agent="\n".join(lines[agent_at + 1:]).strip() if agent_at is not None else None,
        acceptance=_acceptance(agent_lines),
        outside_case=singles.get("outside case"),
        class_none=none_reason(singles.get("class")) or None,
        same_object=singles.get("same object"),
        not_in_ticket=singles.get("not in this ticket", _section(agent_lines, _NOT_IN_HEADING)),
        weight_text=singles.get("weight"),
        adds=tuple(item.strip() for item in re.split(r"[,;]", singles.get("adds") or "") if item.strip()),
        park_lines=tuple(line.strip() for _, line in plain if line.strip().startswith(PARK_PREFIX)),
        labels=frozenset(label for label in labels if isinstance(label, str)),
    )


def none_reason(text: str | None) -> str | None:
    """For a line field written `none (<why>)` (or `none - <why>`): the why, `""` when the
    why is missing; None when the text is not a `none` at all."""

    found = _NONE.fullmatch((text or "").strip())
    if not found:
        return None
    return (found.group("paren") or found.group("after") or "").strip()


def _outside_fences(lines: list[str]) -> list[tuple[int, str]]:
    """Every line outside fenced code with its index; an unclosed fence runs to the end."""

    kept, fence = [], None
    for i, line in enumerate(lines):
        found = _FENCE.match(line)
        if found and (fence is None or found.group(1) == fence):
            fence = found.group(1) if fence is None else None
        elif fence is None:
            kept.append((i, line))
    return kept


def _heading_at(plain: list[tuple[int, str]], heading: str, start: int) -> int | None:
    """The index of the first line from `start` that is the heading: any letter case, closing
    `#`s or a colon allowed."""

    for i, line in plain:
        if i >= start and line.strip().rstrip("#").rstrip().rstrip(":").rstrip().lower() == heading.lower():
            return i
    return None


def _only_park_lines_above(lines: list[str]) -> bool:
    in_comment = False
    for line in lines:
        stripped = line.strip()
        if in_comment or stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
        elif stripped and not stripped.startswith(PARK_PREFIX):
            return False
    return True


def _line_name(line: str) -> tuple[str, str] | None:
    """(name, text) of a `Name: text` line, a bullet and bold around the name allowed."""

    stripped = _BULLET.sub("", line.strip(), count=1) if _BULLET.match(line.strip()) else line.strip()
    bold = _BOLD_NAME.match(stripped)
    if bold:
        return bold.group(1).strip(), stripped[bold.end():].strip()
    name, colon, rest = stripped.partition(":")
    return (name.strip(), rest.strip()) if colon and name.strip() else None


def _human_fields(block: list[str]) -> tuple[HumanField, ...]:
    """Each bullet `**<one of the five>:**` with its continuation lines; a heading, a blank line
    or another bullet ends a field. A bold bullet of another name is no field and ends one."""

    fields: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in block:
        stripped = line.strip()
        if _BULLET.match(stripped) and _BOLD_NAME.match(_BULLET.sub("", stripped, count=1)):
            name, text = _line_name(stripped)
            current = None
            if name in HUMAN_FIELDS:
                current = [text]
                fields.append((name, current))
        elif not stripped or stripped.startswith("#") or _BULLET.match(stripped):
            current = None
        elif current is not None:
            current.append(stripped)
    return tuple(HumanField(name, " ".join(part for part in parts if part)) for name, parts in fields)


_LINE_FIELDS = ("outside case", "class", "same object", "not in this ticket", "weight", "adds")


def _line_fields(lines: list[str]) -> dict[str, str]:
    """The first line of each line field (name in any letter case), its text stripped, maybe
    empty."""

    found: dict[str, str] = {}
    for line in lines:
        named = _line_name(line)
        if named and named[0].lower() in _LINE_FIELDS:
            found.setdefault(named[0].lower(), named[1])
    return found


def _section(lines: list[str], heading: re.Pattern) -> str | None:
    """The text under a heading up to the next heading, joined; None without the heading."""

    for i, line in enumerate(lines):
        if heading.fullmatch(line.strip()):
            body = []
            for rest in lines[i + 1:]:
                if rest.lstrip().startswith("#"):
                    break
                body.append(rest.strip())
            return " ".join(part for part in body if part) or None
    return None


def _cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", inner)]


def _acceptance(lines: list[str]) -> tuple[AcceptanceRow, ...] | None:
    """The rows of the first table whose header has a `check` and a `pass` column."""

    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        table = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            table.append(_cells(lines[i]))
            i += 1
        header = [cell.lower() for cell in table[0]]
        if "check" not in header or "pass" not in header or len(table) < 2:
            continue
        if not all(re.fullmatch(r":?-+:?", cell) for cell in table[1] if cell):
            continue
        check, passes = header.index("check"), header.index("pass")
        rows = []
        for cells in table[2:]:
            cells += [""] * (len(header) - len(cells))
            marked = _AFTER_MERGE.fullmatch(cells[0])
            row_id = marked.group(1).strip() if marked else cells[0]
            rows.append(AcceptanceRow(row_id, cells[check], cells[passes], bool(marked)))
        return tuple(rows)
    return None
