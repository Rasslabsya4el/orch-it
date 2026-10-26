"""How a text names a skill (#397 decisions 4 and 5).

A skill is called by a sentence naming the Skill tool (any letter case, `Skill` or **Skill** too) and
then the skill in quotes or backticks, his form ("Call the Skill tool with `tdd`", "Call the Skill
tool twice, for "grilling" and "domain-modeling""): the first run of quoted names after the tool, so
"with `tdd`, then run `pytest`" calls `tdd` alone. The sentence may run across lines of one
paragraph; "e.g." does not end it.
"""

from __future__ import annotations

import bisect
import re

_SKILL_TOOL = re.compile(r"\b[`*_]{0,2}skill[`*_]{0,2}\s+tool\b", re.IGNORECASE)
_QUOTED_NAME = re.compile(r"[\"`“]([a-z0-9][a-z0-9._:-]*)[\"`”]")
_NEXT_NAME = re.compile(r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)(?=[\"`“])")
_SENTENCE_END = re.compile(r"(?<!e\.g)(?<!i\.e)[.!?;](?=\s|$)")


def _names_after(rest: str) -> list[str]:
    """The run of quoted names the sentence `rest` gives first: `"a"`, `"a" and "b"`, `"a", "b" or "c"`."""

    end = _SENTENCE_END.search(rest)
    clause = rest[:end.start()] if end else rest
    first = _QUOTED_NAME.search(clause)
    if first is None:
        return []
    names, at = [first.group(1)], first.end()
    while (joint := _NEXT_NAME.match(clause, at)) and (name := _QUOTED_NAME.match(clause, joint.end())):
        names.append(name.group(1))
        at = name.end()
    return names


def _calls(text: str) -> list[tuple[int, str]]:
    """(line number, name) of each Skill tool call in `text`, the line being the one naming the tool."""

    found = []
    paragraph: list[tuple[int, str]] = []
    for number, line in [*enumerate(text.split("\n"), 1), (0, "")]:
        if line.strip():
            paragraph.append((number, line))
            continue
        if paragraph:
            parts, starts, length = [], [], 0
            for _, part in paragraph:
                starts.append(length)
                parts.append(" ".join(part.split()) + " ")
                length += len(parts[-1])
            joined = "".join(parts)
            for match in _SKILL_TOOL.finditer(joined):
                at_line = paragraph[bisect.bisect_right(starts, match.start()) - 1][0]
                found += [(at_line, name) for name in _names_after(joined[match.end():match.end() + 2000])]
        paragraph = []
    return found


def skill_calls(text: str) -> set[str]:
    """The names a text calls through the Skill tool."""

    return {name for _, name in _calls(text)}
