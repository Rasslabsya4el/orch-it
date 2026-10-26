"""The brain port's rule table and its check (orchestration-layer#403 acceptance rows 1, 2, 3 and 9).

`docs/research/brain-port-table.md` holds one row per sentence-rule of the four sources the brain's
rules were ported from (their snapshots: `tests/fixtures/brain_port/`). `rules(source, text)` splits a
source into its sentence-rules: each frontmatter line, heading and table row is one; a paragraph or a
list item is split at a sentence end (`.`, `!` or `?`, then a space and a capital, a quote, a backtick,
a bold mark or a bracket). `check(sources, table, repo)` returns one problem per source rule with no
row, row with no source rule, bad verdict, `kept` rule not found at its new place, or `waits on part N`
rule whose new place says nothing of part N.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SOURCES = ("brain.md", "brain-reference.md", "agent-brain.md", "note.md")
VERDICT = re.compile(r"^(kept|changed: .+|dropped: .*#\d+\b.*|waits on part [0-9]+: .+)$")
WINDOW_LINES = 12  # a sentence of the new text may wrap over this many lines from its place
_ITEM = re.compile(r"^\s*(?:[-*] |\d+\. )")
_ITEMS = re.compile(r"(?m)^\s*(?:[-*] |\d+\. )")
_HEADING = re.compile(r"^#{1,6} ")
_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
_END = re.compile(r"(?<=[.!?])([\"»)]?)\s+(?=[A-Z«\"`*(\[])")
_PLACE = re.compile(r"^([^`:]+):(\d+)$")


@dataclass(frozen=True)
class Rule:
    source: str
    line: int
    text: str


@dataclass(frozen=True)
class Row:
    number: int
    source: str
    line: int
    text: str
    place: str
    verdict: str


def norm(text: str) -> str:
    return " ".join(text.split())


def rules(source: str, text: str) -> list[Rule]:
    lines = text.removeprefix("﻿").replace("\r\n", "\n").split("\n")
    found: list[Rule] = []
    start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
        found += [Rule(source, i + 1, norm(lines[i])) for i in range(1, end) if lines[i].strip()]
        start = end + 1
    block: list[tuple[int, str]] = []

    def flush():
        if block:
            whole = norm(" ".join(part for _, part in block))
            whole = _ITEM.sub("", whole, count=1) if _ITEM.match(block[0][1]) else whole
            found.extend(Rule(source, block[0][0], sentence) for sentence in _sentences(whole))
            block.clear()

    for i in range(start, len(lines)):
        line = lines[i]
        if not line.strip():
            flush()
        elif _HEADING.match(line) or line.lstrip().startswith("|"):
            flush()
            if not _SEPARATOR.match(line.strip()):
                found.append(Rule(source, i + 1, norm(line)))
        elif _ITEM.match(line):
            flush()
            block.append((i + 1, line))
        else:
            block.append((i + 1, line))
    flush()
    return found


def _sentences(text: str) -> list[str]:
    parts, last = [], 0
    for match in _END.finditer(text):
        parts.append(text[last:match.start()] + match.group(1))
        last = match.end()
    parts.append(text[last:])
    return [norm(part) for part in parts if norm(part)]


def escape(text: str) -> str:
    return text.replace("|", "\\|")


def _cells(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip()[1:-1])
    return [cell.strip().replace("\\|", "|") for cell in cells]


def rows(table: str) -> list[Row]:
    """The rule rows of the table: five cells, the first a number."""

    found = []
    for line in table.split("\n"):
        if not line.startswith("| ") or _SEPARATOR.match(line.strip()):
            continue
        cells = _cells(line)
        if len(cells) != 5 or not cells[0].isdigit():
            continue
        source, _, number = cells[1].strip("`").rpartition(":")
        found.append(Row(int(cells[0]), source, int(number) if number.isdigit() else 0, norm(cells[2]),
                         cells[3].strip("`"), cells[4]))
    return found


def check(sources: dict[str, str], table: str, repo: Path) -> list[str]:
    """Every problem of the table against the sources; [] when it accounts for every rule: one row per
    rule (a sentence said twice in a source takes two rows), at the rule's own source line."""

    wanted: dict[tuple[str, str], list[Rule]] = {}
    for name, text in sources.items():
        for rule in rules(name, text):
            wanted.setdefault((rule.source, rule.text), []).append(rule)
    held: dict[tuple[str, str], list[Row]] = {}
    for row in rows(table):
        held.setdefault((row.source, norm(row.text)), []).append(row)
    problems = []
    for key, said in wanted.items():
        found = held.get(key, [])
        problems += [f"{rule.source}:{rule.line}: no row: {rule.text[:80]}" for rule in said[len(found):]]
        problems += [f"row {row.number}: a second row for one rule of {row.source}" for row in found[len(said):]]
        lines = {rule.line for rule in said}
        for row in found[:len(said)]:
            if row.line not in lines:
                problems.append(f"row {row.number}: line {row.line} of {row.source} holds no such rule")
            problems += _judged(row, repo)
    for key, found in held.items():
        if key not in wanted:
            problems += [f"row {row.number}: no such rule in {row.source}: {row.text[:80]}" for row in found]
    return problems


def _judged(row: Row, repo: Path) -> list[str]:
    if not VERDICT.match(row.verdict):
        return [f"row {row.number}: no verdict: {row.verdict[:60]!r}"]
    if row.verdict.startswith("dropped"):
        return []
    problems = _placed(row, repo)
    if problems:
        return problems
    if row.verdict == "kept" and not (row.text in _window(row, repo) and row.text not in _window(row, repo, after=1)):
        return [f"row {row.number}: kept, but not found at {row.place}: {row.text[:60]}"]
    if row.verdict.startswith("waits"):
        part = re.match(r"waits on part (\d+)", row.verdict).group(1)
        if not re.search(rf"\bwaits? on part {part}\b", _window(row, repo, paragraph=True), re.IGNORECASE):
            return [f"row {row.number}: waits, but {row.place} says nothing of part {part}"]
    return []


def _placed(row: Row, repo: Path) -> list[str]:
    found = _PLACE.match(row.place)
    if not found or not (repo / found.group(1)).is_file():
        return [f"row {row.number}: no new place: {row.place!r}"]
    if not (repo / found.group(1)).resolve().is_relative_to(repo.resolve()):
        return [f"row {row.number}: {row.place} is outside the repo"]
    try:
        lines = (repo / found.group(1)).read_text("utf-8").split("\n")
    except UnicodeDecodeError:
        return [f"row {row.number}: {row.place} is not UTF-8"]
    if not 1 <= int(found.group(2)) <= len(lines):
        return [f"row {row.number}: {row.place} is past the file's end"]
    return []


def _window(row: Row, repo: Path, *, paragraph: bool = False, after: int = 0) -> str:
    """The new text from the row's place on (`after` lines past it; from the start of its paragraph,
    when `paragraph`: a `Waits on part N` line opens the paragraph it covers), list marks left out,
    white space folded."""

    found = _PLACE.match(row.place)
    lines = (repo / found.group(1)).read_text("utf-8").split("\n")
    at = start = int(found.group(2)) - 1 + after
    while paragraph and start > 0 and lines[start - 1].strip() and not _HEADING.match(lines[start - 1]):
        start -= 1
    return norm(_ITEMS.sub("", "\n".join(lines[start:at + WINDOW_LINES])))
