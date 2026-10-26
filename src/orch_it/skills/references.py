"""How a text names a skill (#397 decisions 4 and 5).

A skill is called by a sentence naming the Skill tool and the skill in quotes or backticks, his form
("Call the Skill tool with `tdd`", "Call the Skill tool twice, for "grilling" and "domain-modeling"");
the sentence may run across lines of one paragraph.
"""

from __future__ import annotations

import re

_SKILL_TOOL = re.compile(r"\bSkill tool\b")
_QUOTED_NAME = re.compile(r"[\"`“]([a-z0-9][a-z0-9._:-]*)[\"`”]")
_SENTENCE_END = re.compile(r"[.!?;](\s|$)")


def _calls(text: str) -> list[tuple[int, str]]:
    """(line number, name) of each Skill tool call in `text`, the line being the one naming the tool."""

    found = []
    paragraph: list[tuple[int, str]] = []
    for number, line in [*enumerate(text.split("\n"), 1), (0, "")]:
        if line.strip():
            paragraph.append((number, line))
            continue
        if paragraph:
            joined, starts = "", []
            for at, part in paragraph:
                starts.append((len(joined), at))
                joined += " ".join(part.split()) + " "
            for match in _SKILL_TOOL.finditer(joined):
                at_line = max(at for start, at in starts if start <= match.start())
                rest = joined[match.end():]
                end = _SENTENCE_END.search(rest)
                found += [(at_line, name) for name in _QUOTED_NAME.findall(rest[:end.start()] if end else rest)]
        paragraph = []
    return found


def skill_calls(text: str) -> set[str]:
    """The names a text calls through the Skill tool."""

    return {name for _, name in _calls(text)}
