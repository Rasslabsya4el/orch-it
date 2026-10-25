"""The ticket model (#398 decision 1): one parser reads a ticket body into its fields.

A body is read in this order: `Parked:` lines and blank lines at the top, the heading
`## For the human` with the five fields `- **What:**`, `- **Goal:**`, `- **Plan:**`,
`- **Result:**`, `- **Undo:**` (a field runs on over its continuation lines), then the heading
`## For the agent` and everything after it. In the agent block, outside fenced code, the parser
reads the first table whose header names a `check` and a `pass` column (the acceptance table,
its first cell the row id, `<id> after merge` marking a row checked only after the merge) and
the lines `Outside case:`, `Class: none (<why>)`, `Same object:`, `Not in this ticket:` (or a
`### Not in this ticket` section), `Weight:` and `Adds:`.

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

_FIELD = re.compile(r"- \*\*(\w+):\*\*[ \t]*(.*)")
_FENCE = re.compile(r"\s*(```|~~~)")
_AFTER_MERGE = re.compile(r"(.*?)\s*\(?after merge\)?", re.IGNORECASE)
_NOT_IN_HEADING = re.compile(r"#{2,6}\s*Not in this ticket\s*:?\s*", re.IGNORECASE)


@dataclass(frozen=True)
class AcceptanceRow:
    id: str
    check: str
    passes: str
    after_merge: bool


@dataclass(frozen=True)
class HumanField:
    name: str
    text: str
    position: int  # the field's place among the fields found, 0 first


@dataclass(frozen=True)
class Ticket:
    title: str | None
    has_human_block: bool
    human_block_first: bool  # nothing but `Parked:` and blank lines above the heading
    human_fields: tuple[HumanField, ...]  # every field line found, in body order, repeats kept
    agent: str | None  # the agent block's text, None without its heading
    acceptance: tuple[AcceptanceRow, ...] | None  # None without an acceptance table
    outside_case: str | None
    class_none: str | None  # the why of `Class: none (<why>)`
    same_object: str | None
    not_in_ticket: str | None
    weight_text: str | None  # what follows `Weight:`
    adds: tuple[str, ...]  # the items of `Adds:`, e.g. `step claim`, `role launcher`
    park_lines: tuple[str, ...]  # the `Parked:` lines at the top
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
        """`Weight: <n>` as a whole number, any words after it ignored; None when unreadable."""

        if self.weight_text is None:
            return None
        found = re.match(r"(\d{1,3})(?!\d|\.\d)", self.weight_text)
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
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n").removeprefix("﻿")
    lines = text.split("\n")
    human_at = _heading_at(lines, HUMAN_HEADING, 0)
    agent_at = _heading_at(lines, AGENT_HEADING, 0 if human_at is None else human_at + 1)

    top = lines[:human_at if human_at is not None else agent_at if agent_at is not None else len(lines)]
    park_lines = tuple(line.strip() for line in _until_text(top) if line.strip().startswith(PARK_PREFIX))
    first = human_at is not None and all(
        not line.strip() or line.strip().startswith(PARK_PREFIX) for line in top)

    human_fields: tuple[HumanField, ...] = ()
    if human_at is not None:
        end = agent_at if agent_at is not None else len(lines)
        human_fields = _human_fields(lines[human_at + 1:end])

    agent_lines = lines[agent_at + 1:] if agent_at is not None else []
    plain = _outside_fences(agent_lines)
    singles = _line_fields(plain)
    return Ticket(
        title=title,
        has_human_block=human_at is not None,
        human_block_first=first,
        human_fields=human_fields,
        agent="\n".join(agent_lines).strip() if agent_at is not None else None,
        acceptance=_acceptance(plain),
        outside_case=singles.get("Outside case"),
        class_none=_class_none(singles.get("Class")),
        same_object=singles.get("Same object"),
        not_in_ticket=singles.get("Not in this ticket", _section(plain, _NOT_IN_HEADING)),
        weight_text=singles.get("Weight"),
        adds=tuple(item.strip() for item in re.split(r"[,;]", singles.get("Adds") or "") if item.strip()),
        park_lines=park_lines,
        labels=frozenset(label for label in labels if isinstance(label, str)),
    )


def _heading_at(lines: list[str], heading: str, start: int) -> int | None:
    """The index of the first line, outside fenced code, that is the heading (any letter case)."""

    fenced = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and i >= start and line.strip().lower() == heading.lower():
            return i
    return None


def _until_text(lines: list[str]) -> list[str]:
    """The lines above the human block up to the first that is neither blank nor `Parked:`."""

    kept = []
    for line in lines:
        if line.strip() and not line.strip().startswith(PARK_PREFIX):
            break
        kept.append(line)
    return kept


def _outside_fences(lines: list[str]) -> list[str]:
    kept, fenced = [], False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            kept.append(line)
    return kept


def _human_fields(block: list[str]) -> tuple[HumanField, ...]:
    """Each `- **<one of the five>:**` line with its continuation lines; a heading, a blank line
    or another field line ends a field. A bold field of another name is no field and ends one."""

    fields: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in _outside_fences(block):
        found = _FIELD.match(line.strip())
        if found:
            current = None
            if found.group(1) in HUMAN_FIELDS:
                current = [found.group(2).strip()]
                fields.append((found.group(1), current))
        elif not line.strip() or line.lstrip().startswith(("#", "- ")):
            current = None
        elif current is not None:
            current.append(line.strip())
    return tuple(HumanField(name, " ".join(part for part in parts if part), i)
                 for i, (name, parts) in enumerate(fields))


_LINE_FIELDS = ("Outside case", "Class", "Same object", "Not in this ticket", "Weight", "Adds")


def _line_fields(lines: list[str]) -> dict[str, str]:
    """The first `<name>: <text>` line of each line field; its text stripped, maybe empty."""

    found: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        for name in _LINE_FIELDS:
            if name not in found and stripped.startswith(f"{name}:"):
                found[name] = stripped[len(name) + 1:].strip()
    return found


def _class_none(text: str | None) -> str | None:
    """The why of `Class: none (<why>)`; None when the line is absent or has no why."""

    found = re.fullmatch(r"none\s*\((.*)\)\s*\.?", text or "", re.IGNORECASE)
    return found.group(1).strip() or None if found else None


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
            row_id, after_merge = cells[0], False
            marked = _AFTER_MERGE.fullmatch(row_id)
            if marked:
                row_id, after_merge = marked.group(1).strip(), True
            rows.append(AcceptanceRow(row_id, cells[check], cells[passes], after_merge))
        return tuple(rows)
    return None
