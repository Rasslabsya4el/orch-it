"""How a text names a skill, and the check that every text of the repo names only skills it holds
(#397 decisions 4 and 5).

A text names a skill in three forms:
- a call: a sentence naming the Skill tool and the skill in quotes or backticks, his form ("Call the
  Skill tool with `tdd`", "Call the Skill tool twice, for "grilling" and "domain-modeling""); the
  sentence may run across lines of one paragraph;
- a preload: an item of the `skills:` field in the frontmatter of a role file (`.claude/agents/`);
- a slash command, `/name`, counted only when `name` is one of his skills the repo does not hold
  (left out, or gone upstream), since `/clear` or a `/settings` route are no skill.

`check` reads every Markdown file of the repo (role files, skills, docs) and returns one line per
name that is neither a copy of his nor ours (a folder of `.claude/skills/` with a `SKILL.md`), and
per use of the namespaced form of his plugin (`mattpocock-skills:<name>`), which the repo's plugin
setting switches off.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from orch_it.skills.copies import SKILL_FILE, SKILLS_FOLDER, Manifest

NAMESPACE = "mattpocock-skills"
LEFT_OUT_FOLDERS = (".git", ".venv", "venv", ".claude/worktrees", "node_modules", "build", "dist",
                    "tests/fixtures", ".pytest_cache", "__pycache__")
_SKILL_TOOL = re.compile(r"\bSkill tool\b")
_QUOTED_NAME = re.compile(r"[\"`“]([a-z0-9][a-z0-9._:-]*)[\"`”]")
_SENTENCE_END = re.compile(r"[.!?;](\s|$)")
_NAMESPACED = re.compile(re.escape(NAMESPACE) + r":([a-z0-9][a-z0-9._-]*)")
_SLASH = re.compile(r"(?<![\w/.~:-])/([a-z0-9][a-z0-9-]*)(?![\w/.-])")
_PRELOAD_ITEM = re.compile(r"[ \t]+-[ \t]+[\"']?([^\"'\s#]+)")
NOT_HELD = "which the repo holds neither as a copy nor as ours"


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


def _preloads(text: str) -> list[tuple[int, str]]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return []
    found, inside = [], False
    for number, line in enumerate(lines[1:], 2):
        if line.strip() == "---":
            break
        if line.startswith("skills:"):
            inside = True
            inline = line[len("skills:"):].strip().strip("[]")
            found += [(number, item.strip().strip("\"'")) for item in inline.split(",") if item.strip()]
        elif inside and _PRELOAD_ITEM.match(line):
            found.append((number, _PRELOAD_ITEM.match(line).group(1)))
        elif line[:1] not in (" ", "\t"):
            inside = False
    return found


def _markdown_files(repo: Path):
    for folder, dirs, files in os.walk(repo):
        here = Path(folder).relative_to(repo).as_posix()
        dirs[:] = sorted(d for d in dirs if not d.endswith(".egg-info")
                         and (d if here == "." else f"{here}/{d}") not in LEFT_OUT_FOLDERS)
        for name in sorted(files):
            if name.endswith(".md"):
                yield Path(folder) / name


def held(repo: Path) -> set[str]:
    """The skills the repo holds: every folder of `.claude/skills/` with a `SKILL.md`."""

    root = repo / SKILLS_FOLDER
    return {p.name for p in root.iterdir() if (p / SKILL_FILE).is_file()} if root.is_dir() else set()


def check(repo: str | Path) -> list[str]:
    """One line per name the repo's texts use for a skill it does not hold, sorted by file and line."""

    repo = Path(repo)
    holds = held(repo)
    manifest = Manifest.load(repo)
    his_not_held = {name: entry["why"] for name, entry in (manifest.not_copied if manifest else {}).items()}
    gone = dict(manifest.gone) if manifest else {}
    his_not_held |= gone
    problems = []
    for path in _markdown_files(repo):
        rel = path.relative_to(repo).as_posix()
        try:
            text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError):
            problems.append((rel, 0, f"{rel}: not UTF-8 text"))
            continue
        for number, line in enumerate(text.split("\n"), 1):
            for name in _NAMESPACED.findall(line):
                problems.append((rel, number, f"{rel}:{number}: the namespaced form `{NAMESPACE}:{name}`;"
                                              f" name the copy `{name}`"))
            for name in _SLASH.findall(line):
                if name in his_not_held and name not in holds:
                    problems.append((rel, number, f"{rel}:{number}: names `/{name}`, his skill the repo does"
                                                  f" not copy ({his_not_held[name]})"))
        uses = [("calls", n, name) for n, name in _calls(text) if ":" not in name]
        if rel.startswith(".claude/agents/"):
            uses += [("preloads", n, name) for n, name in _preloads(text)]
        for verb, number, name in uses:
            if name not in holds:
                why = f" ({gone[name]})" if name in gone else ""
                problems.append((rel, number, f"{rel}:{number}: {verb} `{name}`, {NOT_HELD}{why}"))
    return [line for _, _, line in sorted(problems)]
