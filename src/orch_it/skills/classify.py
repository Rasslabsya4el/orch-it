"""The update classifier (#397 decision 4): the skills of the pinned upstream tree and of his newer
one, compared file by file, each skill given one class.

- `same`: every file equal, byte for byte.
- `added`: a skill folder the pinned tree had not.
- `renamed`: a folder gone from the pinned tree that comes back under a new path: with the same
  files, or by a rename git reports between the two commits (`renames`, old path -> new path).
  A move to another group is a new path too.
- `removed`: a folder gone with nothing to match it.
- `contract_changed`: what the skill takes or returns changed: its frontmatter `name`,
  `description`, `disable-model-invocation` or `argument-hint`; a section whose heading names what
  it takes or returns (inputs, outputs, invocation, arguments, files, formats, templates...); a
  `<...-template>` block; the skills it calls through the Skill tool; a file of the folder added or
  removed, or a template or format file of it changed; a `SKILL.md` with no readable frontmatter.
- `text_changed`: any other difference, down to one line inside a paragraph.

`why` names what decided the class: the fields, sections, templates, calls or files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from orch_it.skills.copies import SKILL_FILE, Tree, blob_hash, skill_name
from orch_it.skills.references import skill_calls

CLASSES = ("same", "added", "renamed", "removed", "text_changed", "contract_changed")
CONTRACT_FIELDS = ("name", "description", "disable-model-invocation", "argument-hint")
_CONTRACT_HEADING = re.compile(
    r"\b(inputs?|outputs?|returns?|arguments?|parameters?|invocation|usage|preconditions?|templates?"
    r"|formats?|files?|deliverables?|definition of done|acceptance criteria|reads?|writes?|produces?)\b")
_CONTRACT_FILE = re.compile(r"format|template", re.IGNORECASE)
_HEADING = re.compile(r"(#{1,6})[ \t]+(.+?)[ \t#]*$")
_FENCE = re.compile(r"[ \t]{0,3}(```|~~~)")
_TEMPLATE = re.compile(r"<([a-z0-9-]+-template)>.*?</\1>", re.DOTALL)
_KEY = re.compile(r"([A-Za-z0-9_-]+):(.*)$")


@dataclass(frozen=True)
class Change:
    name: str                 # the skill's name in the newer tree, or in the pinned one when removed
    cls: str                  # one of CLASSES
    old_path: str | None
    new_path: str | None
    why: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.cls not in CLASSES:
            raise ValueError(f"not a class: {self.cls}")

    @property
    def old_name(self) -> str | None:
        return skill_name(self.old_path) if self.old_path else None


def classify(old: Tree, new: Tree, *, renames: dict[str, str] | None = None) -> list[Change]:
    """One change per skill of either tree, sorted by name."""

    renames = renames or {}
    changes = []
    for path in sorted(old.keys() & new.keys()):
        changes.append(_compare(skill_name(path), path, path, old[path], new[path]))
    gone = sorted(old.keys() - new.keys())
    fresh = sorted(new.keys() - old.keys())
    for path in gone:
        target = renames.get(path)
        if target not in fresh:
            target = next((n for n in fresh if _identity(new[n]) == _identity(old[path])), None)
        if target is None:
            changes.append(Change(skill_name(path), "removed", path, None))
            continue
        fresh.remove(target)
        compared = _compare(skill_name(target), path, target, old[path], new[target])
        changes.append(Change(skill_name(target), "renamed", path, target, compared.why))
    changes += [Change(skill_name(path), "added", None, path) for path in fresh]
    return sorted(changes, key=lambda c: (c.name, c.old_path or ""))


def _identity(files: dict[str, bytes]) -> frozenset:
    return frozenset((rel, blob_hash(data)) for rel, data in files.items())


def _compare(name: str, old_path: str, new_path: str, old: dict[str, bytes], new: dict[str, bytes]) -> Change:
    if old == new:
        return Change(name, "same", old_path, new_path)
    changed = sorted(rel for rel in old.keys() & new.keys() if old[rel] != new[rel])
    contract = [f"files: +{rel}" for rel in sorted(new.keys() - old.keys())]
    contract += [f"files: -{rel}" for rel in sorted(old.keys() - new.keys())]
    for rel in changed:
        if rel == SKILL_FILE:
            contract += _skill_contract(old[rel], new[rel])
        elif _CONTRACT_FILE.search(PurePosixPath(rel).name):
            contract.append(rel)
    if contract:
        return Change(name, "contract_changed", old_path, new_path, contract)
    return Change(name, "text_changed", old_path, new_path, changed)


def _skill_contract(old: bytes, new: bytes) -> list[str]:
    try:
        old_text, new_text = (data.decode("utf-8").replace("\r\n", "\n") for data in (old, new))
    except UnicodeDecodeError:
        return [f"{SKILL_FILE} unreadable"]
    old_front, old_body = _split(old_text)
    new_front, new_body = _split(new_text)
    if old_front is None or new_front is None:
        return ["frontmatter"]
    why = [key for key in CONTRACT_FIELDS if old_front.get(key) != new_front.get(key)]
    old_sections, new_sections = _contract_sections(old_body), _contract_sections(new_body)
    for heading in [*new_sections, *(h for h in old_sections if h not in new_sections)]:
        if old_sections.get(heading) != new_sections.get(heading):
            why.append(f"section {heading}")
    old_templates, new_templates = _templates(old_body), _templates(new_body)
    for tag in sorted(old_templates.keys() | new_templates.keys()):
        if old_templates.get(tag) != new_templates.get(tag):
            why.append(f"template {tag}")
    old_calls, new_calls = skill_calls(old_body), skill_calls(new_body)
    why += [f"calls +{n}" for n in sorted(new_calls - old_calls)]
    why += [f"calls -{n}" for n in sorted(old_calls - new_calls)]
    return why


def _split(text: str) -> tuple[dict[str, str] | None, str]:
    """The frontmatter's fields and the body, or (None, text) when there is no closed frontmatter."""

    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 3)
    if end == -1:
        if not text.endswith("\n---"):
            return None, text
        end = len(text) - 4
    fields: dict[str, list[str]] = {}
    current = None
    for line in text[4:end].split("\n"):
        found = _KEY.match(line)
        if found and not line[:1].isspace():
            current = found.group(1)
            fields[current] = [found.group(2)]
        elif current is not None:
            fields[current].append(line)
    return {key: _value(parts) for key, parts in fields.items()}, text[end + 5:]


def _value(parts: list[str]) -> str:
    value = " ".join(part.strip() for part in parts).strip()
    if value[:1] in (">", "|"):
        value = value[1:].lstrip("+-").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].replace('\\"', '"') if value[0] == '"' else value[1:-1].replace("''", "'")
    return " ".join(value.split())


def _contract_sections(body: str) -> dict[str, str]:
    """Each section whose heading names what the skill takes or returns, heading -> its text up to
    the next heading of its level or higher; headings inside code fences are text."""

    headings = []  # (line index, level, title)
    lines = body.split("\n")
    fenced = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        found = None if fenced else _HEADING.fullmatch(line)
        if found:
            headings.append((i, len(found.group(1)), found.group(2).strip()))
    sections = {}
    for n, (start, level, title) in enumerate(headings):
        if not _CONTRACT_HEADING.search(title.lower()):
            continue
        end = next((i for i, lv, _ in headings[n + 1:] if lv <= level), len(lines))
        text = "\n".join(line.rstrip() for line in lines[start:end]).strip()
        sections[title] = sections.get(title, "") + text
    return sections


def _templates(body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _TEMPLATE.finditer(body):
        found[match.group(1)] = found.get(match.group(1), "") + match.group(0)
    return found
