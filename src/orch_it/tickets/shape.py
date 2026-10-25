"""The shape rules (#398 decision 2): `violations` returns every rule a ticket breaks, not the
first, each with its code. The session hook and the GitHub workflow call the same function
(decision 3), and the `write-ticket` skill states the same rules code for code (decision 7).

A missing block is one violation: with no `## For the human` heading the five fields are not
judged, with no `## For the agent` heading nothing of the agent block is. A title left out
(`None`) is not judged; the title rules apply to any title given.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from orch_it.tickets.model import HUMAN_FIELDS, Ticket

TITLE_MAX = 80
WEIGHT_RANGE = range(1, 11)

# code -> the rule as the skill states it
RULES: dict[str, str] = {
    "title_too_long": f"the title is at most {TITLE_MAX} characters",
    "title_parked": "the title never holds the word \"parked\": a park is the `parked` label and its line",
    "title_issue_number": "the title holds no issue number",
    "human_block_missing": "the body has the heading `## For the human`",
    "human_block_not_first": "nothing but `Parked:` lines stands above `## For the human`",
    "human_field_missing": "each of What, Goal, Plan, Result, Undo is there and not empty",
    "human_field_order": "the five fields keep the order What, Goal, Plan, Result, Undo",
    "human_field_not_russian": "the text of each human field is Russian",
    "human_field_path": "a human field holds no file path and no file:line",
    "agent_block_missing": "the body has the heading `## For the agent` after the human block",
    "acceptance_missing": "the agent block has an acceptance table with a `check` and a `pass` column and a row",
    "outside_case_missing": "the agent block has a line `Outside case: <the case> (row <id>)` or `Class: none (<why>)`",
    "outside_case_no_row": "the `Outside case:` line names its acceptance row as `row <id>`",
    "same_object_missing": "the agent block has a line `Same object: <what else acts on it>` or `Same object: none (<why>)`",
    "events_row_missing": "a ticket whose `Adds:` line adds a role or a step has an acceptance row whose check starts with `events`",
    "weight_missing": "the agent block has a line `Weight: <1-10>`",
    "weight_bad": "the weight is a whole number from 1 to 10",
}


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str = ""

    @property
    def message(self) -> str:
        said = f"{self.code}: {RULES[self.code]}"
        return f"{said} ({self.detail})" if self.detail else said


_ISSUE_NUMBER = re.compile(r"(?:[\w.-]+/[\w.-]+)?#\d+")
_PARKED = re.compile(r"parked", re.IGNORECASE)
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"https?://\S+")
# the fixed English words of the Undo field (`two-way door`, `touches`) are the form, not text
_UNDO_WORDS = re.compile(r"\b(?:two-way door|one-way door|one revert|touches)\b", re.IGNORECASE)
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-z]")
_EXTENSIONS = ("py|pyi|md|js|mjs|ts|tsx|json|jsonl|yml|yaml|toml|sql|txt|sh|ps1|bat|cfg|ini|html|css"
               "|xml|csv|go|rs|java|rb|lock")
_PATH = re.compile(
    rf"(?<![\w.])(?:[A-Za-z]:[\\/]|\.{{0,2}}[\\/])?(?:[\w.-]+[\\/])*[\w-][\w.-]*\.(?:{_EXTENSIONS})\b"
    r"(?::\d+(?:-\d+)?)?"
    r"|(?<![\w.])(?:[A-Za-z]:[\\/]|\.{0,2}[\\/])?(?:[\w.-]+[\\/])+[\w.-]+:\d+\b"
    r"|#L\d+")
_ROW = re.compile(r"\brows?\s+([\w.-]+?)(?=[\s,;:)\]]|\.(?:\s|$)|$)", re.IGNORECASE)
_EVENTS_CHECK = re.compile(r"\W*events\b", re.IGNORECASE)
_ADDS_ROLE_OR_STEP = re.compile(r"\b(?:role|step)s?\b", re.IGNORECASE)
_NONE_WITHOUT_WHY = re.compile(r"none\s*(?:\(\s*\))?\s*\.?", re.IGNORECASE)


def violations(ticket: Ticket) -> list[Violation]:
    found = _title(ticket.title)
    found += _human(ticket)
    if ticket.agent is None:
        found.append(Violation("agent_block_missing"))
    else:
        found += _agent(ticket)
    return found


def _title(title: str | None) -> list[Violation]:
    if title is None:
        return []
    found = []
    if len(title) > TITLE_MAX:
        found.append(Violation("title_too_long", f"{len(title)} characters"))
    if _PARKED.search(title):
        found.append(Violation("title_parked"))
    numbers = _ISSUE_NUMBER.findall(title)
    if numbers:
        found.append(Violation("title_issue_number", ", ".join(dict.fromkeys(numbers))))
    return found


def _human(ticket: Ticket) -> list[Violation]:
    if not ticket.has_human_block:
        return [Violation("human_block_missing")]
    found = [] if ticket.human_block_first else [Violation("human_block_not_first")]
    texts = ticket.human
    for name in HUMAN_FIELDS:
        if not texts.get(name, "").strip():
            found.append(Violation("human_field_missing", name))
    furthest = -1
    for field in ticket.human_fields:
        place = HUMAN_FIELDS.index(field.name)
        if place <= furthest:
            found.append(Violation("human_field_order", field.name))
        furthest = max(furthest, place)
    for name, text in texts.items():
        if text.strip() and not _is_russian(text):
            found.append(Violation("human_field_not_russian", name))
        paths = [m.group(0) for m in _PATH.finditer(_URL.sub(" ", text))]
        if paths:
            found.append(Violation("human_field_path", f"{name}: {', '.join(dict.fromkeys(paths))}"))
    return found


def _is_russian(text: str) -> bool:
    """Russian when at least half its letters are Cyrillic, leaving out code spans, links and the
    Undo field's fixed words: a domain term in backticks or a product name stays as written."""

    prose = _UNDO_WORDS.sub(" ", _URL.sub(" ", _CODE_SPAN.sub(" ", text)))
    cyrillic, latin = len(_CYRILLIC.findall(prose)), len(_LATIN.findall(prose))
    return cyrillic > 0 and cyrillic >= latin


def _agent(ticket: Ticket) -> list[Violation]:
    found = []
    rows = ticket.acceptance or ()
    if not rows:
        found.append(Violation("acceptance_missing"))
    if ticket.outside_case:
        named = {m.group(1).lower() for m in _ROW.finditer(ticket.outside_case)}
        if not named & {row.id.lower() for row in rows}:
            found.append(Violation("outside_case_no_row", ", ".join(sorted(named)) or "no row named"))
    elif not ticket.class_none:
        found.append(Violation("outside_case_missing"))
    same = (ticket.same_object or "").strip()
    if not same or _NONE_WITHOUT_WHY.fullmatch(same):
        found.append(Violation("same_object_missing"))
    adds = [item for item in ticket.adds if _ADDS_ROLE_OR_STEP.search(item)]
    if adds and not any(_EVENTS_CHECK.match(row.check) for row in rows):
        found.append(Violation("events_row_missing", ", ".join(adds)))
    if ticket.weight_text is None:
        found.append(Violation("weight_missing"))
    elif ticket.weight not in WEIGHT_RANGE:
        found.append(Violation("weight_bad", ticket.weight_text[:40]))
    return found
