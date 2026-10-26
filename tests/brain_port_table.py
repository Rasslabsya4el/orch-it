"""The brain port's rule table and its check (orchestration-layer#403 acceptance rows 1, 2 and 9).

`docs/research/brain-port-table.md` holds one row per sentence-rule of the four sources the brain's
rules were ported from (their snapshots: `tests/fixtures/brain_port/`). `rules(text)` splits a
source into its sentence-rules: each frontmatter line, heading and table row is one; a paragraph or
a list item is split at a sentence end (`.`, `!` or `?`, then a space and a capital, a quote, a
backtick, a bold mark or a bracket). `check(sources, table, repo)` returns one problem per source
rule with no row, row with no source rule, bad verdict, or `kept` rule not found at its new place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SOURCES = ("brain.md", "brain-reference.md", "agent-brain.md", "note.md")
VERDICT = re.compile(r"^(kept|changed: .+|dropped: .*#\d+.*|waits on part [0-9]+: .+)$")
_ITEM = re.compile(r"^\s*(?:[-*] |\d+\. )")
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
    lines = text.replace("\r\n", "\n").split("\n")
    found: list[Rule] = []
    start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
        found += [Rule(source, i + 1, norm(lines[i])) for i in range(1, end) if lines[i].strip()]
        start = end + 1
    block: list[tuple[int, str]] = []

    def flush():
        if block:
            first = block[0][0]
            whole = norm(" ".join(part for _, part in block))
            whole = _ITEM.sub("", whole, count=1) if _ITEM.match(block[0][1]) else whole
            for sentence in _sentences(whole):
                found.append(Rule(source, first, sentence))
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
    """Every problem of the table against the sources; [] when it accounts for every rule."""

    wanted = [rule for name, text in sources.items() for rule in rules(name, text)]
    held = rows(table)
    problems = []
    by_key: dict[tuple[str, str], list[Row]] = {}
    for row in held:
        by_key.setdefault((row.source, norm(row.text)), []).append(row)
    seen = set()
    for rule in wanted:
        key = (rule.source, rule.text)
        matches = by_key.get(key, [])
        if not matches:
            problems.append(f"{rule.source}:{rule.line}: no row: {rule.text[:80]}")
            continue
        seen.add(key)
        for row in matches:
            if not VERDICT.match(row.verdict):
                problems.append(f"row {row.number}: no verdict: {row.verdict[:60]!r}")
            elif row.verdict == "kept":
                problems += _kept(row, repo)
            elif not row.verdict.startswith("dropped"):
                problems += _placed(row, repo)
    for key, matches in by_key.items():
        if key not in seen:
            problems += [f"row {row.number}: no such rule in {row.source}: {row.text[:80]}" for row in matches]
    return problems


def _placed(row: Row, repo: Path) -> list[str]:
    found = _PLACE.match(row.place)
    if not found or not (repo / found.group(1)).is_file():
        return [f"row {row.number}: no new place: {row.place!r}"]
    lines = (repo / found.group(1)).read_text("utf-8").split("\n")
    if not 1 <= int(found.group(2)) <= len(lines):
        return [f"row {row.number}: {row.place} is past the file's end"]
    return []


def _kept(row: Row, repo: Path) -> list[str]:
    problems = _placed(row, repo)
    if problems:
        return problems
    found = _PLACE.match(row.place)
    lines = (repo / found.group(1)).read_text("utf-8").split("\n")
    at = int(found.group(2)) - 1
    near = norm(" ".join(lines[at:at + 12]))
    near = re.sub(r"^(?:[-*] |\d+\. |#{1,6} )", "", near)
    if row.text not in norm(re.sub(r"(?m)^\s*(?:[-*] |\d+\. )", "", "\n".join(lines[at:at + 12]))) and row.text not in near:
        return [f"row {row.number}: kept, but not found at {row.place}: {row.text[:60]}"]
    return []
