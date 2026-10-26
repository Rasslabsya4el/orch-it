"""Rules held as checks (#399 decision 4): `check(repo)` reads every text an agent of the layer works
from and returns one `Finding` per break of the kinds #382 counted in the old layer's 30 files.

The texts read are whatever exist of: `.claude/agents/*.md`, every Markdown file of a skill of ours
under `.claude/skills/` (a folder the manifest `pocock.json` does not name as his copy), `CLAUDE.md`
and `.claude/CLAUDE.md`, and every Markdown file under `docs/`. Nothing is listed by name, so a role
file added later is read with no edit here.

What is code in a text: an inline code span (`` `...` ``) and a line of a fenced block. A path, read
from inline code and Markdown links only (a fenced block is an example or a command, and in a span of
several words, a command, only a variable's value is read), is a word that
ends in a file extension or a `/`, the value of a variable (`PYTHONPATH=src/x`), or a word whose
first folder is one of the repo's own; a bare `name.md` must be a file somewhere in the repo. A word
with a placeholder (`<n>`, `$X`, `*`, `~`) is no path. A path of another repo is written
`<repo>:<path>` and is not read. A section is named as `path#anchor` (GitHub's heading anchor) or as
`` `path` section "Title" `` (or `` `path` "Title" ``).

Codes:
- `path_missing`: a path that is neither under the repo root nor beside the text;
- `section_missing`: a named section with no heading of that title or anchor in its file;
- `stub`: a pointer to a file, or a section, that holds no text but headings, or only a TODO,
  TBD or stub mark;
- `command_unknown`, `flag_unknown`: a `python -m orch_it ...` command (in code) whose command,
  value or option the command line does not have, or a `python -m orch_it.<module>` that is no
  module; a placeholder (`<n>`, `PATH`, `...`) stands for any value;
- `label_unknown`: a label named (a `gh issue`/`gh pr` label option, "the `x` label", "label `x`")
  that is not in the label set of `orch_it.labels`;
- `role_unknown`: a role named ("the `x` role", "role `x`") that is not in the roles settings;
- `skill_unknown`: a skill named that the repo holds neither as his copy nor as ours (#397's
  `orch_it.skills.references.check`, reused as it is);
- `rule_deferred`: a rule handed to a file that may not be there: a local file (`local/`,
  `*.local.*`), or a pointer said to hold "if it exists", "if present", "its own part of" ...;
- `rule_twice`: two normative sentences (with `must`, `never`, `only` ...; prose, not a table row)
  in two files or two places of one that share most of their content words: one rule said twice,
  or two rules on one object that disagree; the ADRs of `docs/adr/` record why a rule is so and
  are left out of this one;
- `model_literal`, `effort_literal`: a model (`claude-...`, "Opus 4.8") or an effort ("high
  effort") written into a text; they are settings (decision 5), and only an agent file's
  frontmatter, which `roles render` writes, carries them;
- `text_unreadable`: a text that is not UTF-8.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

CODES = ("path_missing", "section_missing", "stub", "command_unknown", "flag_unknown", "label_unknown", "role_unknown",
         "skill_unknown", "rule_deferred", "rule_twice", "model_literal", "effort_literal", "text_unreadable")
AGENTS = ".claude/agents"
SKILLS = ".claude/skills"
FILE_EXTENSIONS = ("md", "py", "json", "jsonl", "toml", "yml", "yaml", "sql", "txt", "sh", "ps1", "cfg", "ini", "xml")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SPAN = re.compile(r"(`+)(.+?)\1")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_TITLED = re.compile(r"`([^`\s]+)`,?\s+(?:section\s+)?[\"“]([^\"”]+)[\"”]")
_SECTION_OF = re.compile(r"section\s+[\"“]([^\"”]+)[\"”]\s+of\s+`([^`\s]+)`")
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")
_NO_PATH = re.compile(r"[<>*?$%{}~@\\|]|://|^[A-Za-z]:|^-|^/")
_EXTENSION = re.compile(r"\.(" + "|".join(FILE_EXTENSIONS) + r")$")
_QUOTES = "\"'()[]"
_TRAILING = ",;.!:"
_ASSIGNED = re.compile(r"[A-Z][A-Z0-9_]*=(.+)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_STUB_MARK = re.compile(r"^\W*(todo|tbd|stub|to be written|coming soon)\W*$", re.IGNORECASE)


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.code}: {self.detail}"


@dataclass(frozen=True)
class _Line:
    number: int
    text: str
    fenced: bool  # the whole line is code
    spans: tuple[str, ...]  # its inline code spans (none on a fenced line)

    def code(self) -> list[str]:
        return [self.text] if self.fenced else list(self.spans)


@dataclass(frozen=True)
class _Text:
    rel: str
    lines: tuple[_Line, ...]


def _lines(text: str) -> tuple[_Line, ...]:
    out, fence = [], None
    for number, line in enumerate(text.split("\n"), 1):
        opened = _FENCE.match(line)
        if fence is None and opened:
            fence = opened.group(1)
            continue
        if fence is not None:
            if line.strip().startswith(fence):
                fence = None
                continue
            out.append(_Line(number, line, True, ()))
            continue
        out.append(_Line(number, line, False, tuple(m.group(2).strip() for m in _SPAN.finditer(line))))
    return tuple(out)


def _ours(repo: Path) -> list[Path]:
    """The skill folders of ours: every folder of `.claude/skills/` the manifest does not name as
    his copy (all of them when the manifest cannot be read; #397's check reports it)."""

    from orch_it.skills.copies import Manifest, SkillsError

    root = repo / SKILLS
    if not root.is_dir():
        return []
    try:
        manifest = Manifest.load(repo)
        his = set(manifest.skills) if manifest else set()
    except SkillsError:
        his = set()
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name not in his)


def text_files(repo: str | Path) -> list[Path]:
    """Every text the check reads, sorted."""

    repo = Path(repo)
    found = list((repo / AGENTS).glob("*.md")) if (repo / AGENTS).is_dir() else []
    for folder in [*_ours(repo), repo / "docs"]:
        if folder.is_dir():
            found += [Path(dirpath) / name for dirpath, _, names in os.walk(folder)
                      for name in names if name.lower().endswith(".md")]
    found += [p for p in (repo / "CLAUDE.md", repo / ".claude" / "CLAUDE.md") if p.exists()]
    return sorted(p for p in found if p.is_file())


def _skipped(rel: str) -> bool:
    parts = set(rel.split("/"))
    return bool({".git", ".venv", "venv", "node_modules", "__pycache__"} & parts) or rel.startswith(".claude/worktrees")


def slug(title: str) -> str:
    """GitHub's anchor of a heading: lower case, markup and punctuation gone, spaces to hyphens."""

    title = re.sub(r"[`*_]", "", title.strip().lower())
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", title))


@dataclass(frozen=True)
class _Section:
    title: str
    anchor: str
    empty: bool


def _sections(text: str) -> tuple[list[_Section], bool]:
    """(the headings of a Markdown text with whether each has no text before the next heading of
    its level or above, whether the whole text is a stub)."""

    heads: list[tuple[int, str, int]] = []
    body_lines: list[int] = []
    lines = [line for line in text.split("\n")]
    fence = None
    for number, line in enumerate(lines):
        if fence is None and _FENCE.match(line):
            fence = _FENCE.match(line).group(1)
            body_lines.append(number)
            continue
        if fence is not None:
            if line.strip().startswith(fence):
                fence = None
            body_lines.append(number)
            continue
        heading = _HEADING.match(line)
        if heading:
            heads.append((len(heading.group(1)), heading.group(2), number))
        elif line.strip() and not _STUB_MARK.match(line) and line.strip() != "---":
            body_lines.append(number)
    sections, seen = [], {}
    for i, (level, title, at) in enumerate(heads):
        end = next((h[2] for h in heads[i + 1:] if h[0] <= level), len(lines))
        anchor = slug(title)
        if anchor in seen:  # GitHub numbers repeated anchors: `x`, `x-1`, ...
            seen[anchor] += 1
            anchor = f"{anchor}-{seen[anchor]}"
        else:
            seen[anchor] = 0
        sections.append(_Section(title, anchor, not any(at < n < end for n in body_lines)))
    return sections, not body_lines


class _Repo:
    """What the texts are judged against: the repo's files."""

    def __init__(self, repo: Path):
        self.root = repo
        self.top = {p.name for p in repo.iterdir()}
        self.md_files: dict[str, list[Path]] = {}
        for dirpath, dirs, names in os.walk(repo):
            rel = Path(dirpath).relative_to(repo).as_posix()
            dirs[:] = [d for d in dirs if not _skipped(f"{rel}/{d}" if rel != "." else d)]
            for name in names:
                if name.endswith(".md"):
                    self.md_files.setdefault(name, []).append(Path(dirpath) / name)

    def locate(self, path: str, beside: str) -> Path | None:
        if "/" not in path.rstrip("/") and path.endswith(".md"):
            if (self.root / beside / path).is_file():
                return self.root / beside / path
            found = self.md_files.get(path)
            return found[0] if found else None
        for base in (self.root, self.root / beside):
            target = base / path
            if target.exists() and (not path.endswith("/") or target.is_dir()):
                return target
        return None


@cache
def _read_sections(path: Path) -> tuple[list[_Section], bool]:
    try:
        return _sections(path.read_bytes().decode("utf-8", "replace").replace("\r\n", "\n"))
    except OSError:
        return [], False


def _path_of(word: str, repo: _Repo) -> tuple[str, str | None] | None:
    """(the path `word` names, its anchor), or None when it is no path of this repo."""

    word = word.strip(_QUOTES).rstrip(_TRAILING)
    assigned = _ASSIGNED.match(word)
    if assigned:  # `PYTHONPATH=src/x`: the value of a variable is a path
        word = assigned.group(1).strip(_QUOTES).rstrip(_TRAILING)
    word, _, anchor = word.partition("#")
    word = _LINE_SUFFIX.sub("", word)
    if not word or _NO_PATH.search(word) or ":" in word or "=" in word:
        return None
    if "/" not in word.rstrip("/"):
        return (word, anchor or None) if word.endswith(".md") else None
    if assigned or _EXTENSION.search(word) or word.endswith("/") or word.split("/", 1)[0] in repo.top:
        return word, anchor or None
    return None


def _pointers(line: _Line) -> list[tuple[str, str | None, str | None]]:
    """(word, anchor-or-None, section title-or-None) of every pointer of a prose line."""

    found = [(word, None, None) for span in line.spans for word in span.split()
             if len(span.split()) == 1 or _ASSIGNED.match(word)]  # a command's words are no pointers
    found += [(target, None, None) for target in _LINK.findall(line.text)
              if not target.startswith(("http:", "https:", "#", "mailto:"))]
    found += [(path, None, title) for path, title in _TITLED.findall(line.text)]
    found += [(path, None, title) for title, path in _SECTION_OF.findall(line.text)]
    return found


def _paths(text: _Text, repo: _Repo) -> list[Finding]:
    beside = str(Path(text.rel).parent)
    found = []
    for line in text.lines:
        if line.fenced:  # a fenced block is an example or a command, never a pointer
            continue
        for word, _, title in _pointers(line):
            named = _path_of(word, repo)
            if named is None:
                continue
            path, anchor = named
            target = repo.locate(path, beside)
            where = (text.rel, line.number)
            if target is None:
                found.append(Finding(*where, "path_missing", f"`{path}` does not exist"))
                continue
            if not target.is_file() or not target.name.endswith(".md"):
                continue
            sections, stub = _read_sections(target)
            if anchor is None and title is None:
                if stub:
                    found.append(Finding(*where, "stub", f"`{path}` holds no text"))
                continue
            section = next((s for s in sections if (anchor and s.anchor == anchor.lower())
                            or (title and s.title.strip("`*_ ").lower() == title.strip().lower())), None)
            name = f"#{anchor}" if anchor else f'"{title}"'
            if section is None:
                found.append(Finding(*where, "section_missing", f"`{path}` has no section {name}"))
            elif section.empty:
                found.append(Finding(*where, "stub", f"`{path}` section {name} holds no text"))
    return found


_COMMAND = re.compile(r"\bpython[0-9.]*(?:\.exe)?\s+-m\s+(orch_it(?:\.[A-Za-z_][\w.]*)?)(?![\w.])(.*)")
_SEPARATOR = re.compile(r"\s(?:\||&&|\|\||;|>|2>|<)\s|`")
_PLACEHOLDER = re.compile(r"[<{$]|\.\.\.|^[A-Z][A-Z0-9_.=-]*$|^#")


def _placeholder(word: str) -> bool:
    return bool(_PLACEHOLDER.search(word))


@cache
def _parser():
    from orch_it import cli

    return cli.parser()


def _judge_command(module: str, rest: str) -> str | None:
    """Why `python -m <module> <rest>` is no command of orch-it, or None."""

    import argparse
    import importlib.util
    import shlex

    if module != "orch_it":
        try:
            known = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            known = False
        return None if known else f"no module `{module}`"
    rest = _SEPARATOR.split(f" {rest} ", 1)[0]
    try:
        words = shlex.split(rest)
    except ValueError:
        words = rest.split()
    words = [w.strip("[]()") for w in words]
    words = [w for w in words if w and w != "|"]
    parser, said = _parser(), "python -m orch_it"
    positionals = [a for a in parser._actions if not a.option_strings]
    i = 0
    while i < len(words):
        word = words[i]
        if word.startswith("-") and len(word) > 1 and not _placeholder(word):
            name, equals, value = word.partition("=")
            action = parser._option_string_actions.get(name)
            if action is None:
                return f"`{said}`: no option `{name}`"
            if action.nargs == 0:
                i += 1
                continue
            value = value if equals else (words[i + 1] if i + 1 < len(words) else None)
            if value is not None and action.choices and not _placeholder(value) and value not in action.choices:
                return f"`{said}`: `{value}` is not one of its `{name}` values"
            i += 1 if equals else 2
            continue
        if not positionals:
            return None  # more words than the command takes: prose after it
        action = positionals[0]
        if isinstance(action, argparse._SubParsersAction):
            if _placeholder(word):
                return None  # `events <command>`: any command of the group
            if word not in action.choices:
                return f"`{said}`: no command `{word}`"
            parser, said = action.choices[word], f"{said} {word}"
            positionals = [a for a in parser._actions if not a.option_strings]
        else:
            if action.choices and not _placeholder(word) and word not in action.choices:
                return f"`{said}`: `{word}` is not one of its `{action.dest}` values"
            if action.nargs not in ("*", "+", argparse.REMAINDER):
                positionals = positionals[1:]
        i += 1
    return None


def _commands(text: _Text) -> list[Finding]:
    found = []
    for line in text.lines:
        for code in (part for piece in line.code() for part in _SEPARATOR.split(f" {piece} ")):
            for match in _COMMAND.finditer(code):
                why = _judge_command(match.group(1), match.group(2))
                if why:
                    code_of = "flag_unknown" if ": no option" in why else "command_unknown"
                    found.append(Finding(text.rel, line.number, code_of, why))
    return found


_LABEL_FLAG = re.compile(r"(?:--add-label|--remove-label|--label|-l)(?:\s+|=)(\"[^\"]*\"|'[^']*'|\S+)")
_GH_ISSUE = re.compile(r"\bgh\s+(?:issue|pr)\b")
_NAMED_AS = r"`([^`\s]+(?: [^`\s]+)?)`\s+{word}\b|\b{word}\s+`([^`\s]+(?: [^`\s]+)?)`"
_NAMED_LABEL = re.compile(_NAMED_AS.format(word="label"))
_NAMED_ROLE = re.compile(_NAMED_AS.format(word="role"))


@cache
def _labels() -> frozenset[str]:
    from orch_it import labels

    return frozenset(labels.label_set())


@cache
def _roles() -> frozenset[str]:
    from orch_it.roles import RoleSet

    return frozenset(RoleSet.load().roles)


def _names(text: _Text) -> list[Finding]:
    """Labels a text names (a `gh issue` or `gh pr` label option, "the `x` label", "label `x`")
    against the label set, and roles ("the `x` role", "role `x`") against the roles settings."""

    found = []
    for line in text.lines:
        labels: list[str] = []
        for code in line.code():
            if _GH_ISSUE.search(code):
                labels += [name.strip() for value in _LABEL_FLAG.findall(code)
                           for name in value.strip("\"'").split(",") if name.strip()]
        roles: list[str] = []
        if not line.fenced:
            labels += [a or b for a, b in _NAMED_LABEL.findall(line.text)]
            roles += [a or b for a, b in _NAMED_ROLE.findall(line.text)]
        for name in dict.fromkeys(labels):
            if name not in _labels() and not _placeholder(name):
                found.append(Finding(text.rel, line.number, "label_unknown", f"`{name}` is no label of the label set"))
        for name in dict.fromkeys(roles):
            if name not in _roles() and not _placeholder(name):
                found.append(Finding(text.rel, line.number, "role_unknown", f"`{name}` is no role of the roles settings"))
    return found


_MODEL = re.compile(r"\bclaude-(?:opus|sonnet|haiku|fable|mythos)-\d[\w.-]*"
                    r"|\b(?:Opus|Sonnet|Haiku|Fable|Mythos)\s+\d+(?:\.\d+)?\b")
_EFFORT = re.compile(r"\beffort\b\W{0,3}(?:of\s+|at\s+|is\s+)?\b(?:low|medium|high|xhigh|max)\b"
                     r"|\b(?:low|medium|high|xhigh|max)\s+effort\b", re.IGNORECASE)


def _frontmatter_end(text: _Text) -> int:
    """The last line of an agent file's frontmatter (rendered from the settings), else 0."""

    if not text.rel.startswith(AGENTS + "/") or not text.lines or text.lines[0].text.strip() != "---":
        return 0
    return next((line.number for line in text.lines[1:] if line.text.strip() == "---"), 0)


def _literals(text: _Text) -> list[Finding]:
    """A model or effort written into a text: they are settings (decision 5), read from there."""

    found = []
    skip = _frontmatter_end(text)
    for line in text.lines:
        if line.number <= skip:
            continue
        for match in _MODEL.finditer(line.text):
            found.append(Finding(text.rel, line.number, "model_literal",
                                 f"`{match.group(0)}`: a role's model is a setting of the roles settings"))
        for match in _EFFORT.finditer(line.text):
            found.append(Finding(text.rel, line.number, "effort_literal",
                                 f"`{match.group(0)}`: a role's effort is a setting of the roles settings"))
    return found


_LOCAL_PATH = re.compile(r"(?:^|/)local(?:/|$)|\.local\.")
_OPTIONAL = re.compile(r"\b(?:if (?:it|one|that file|the file|such a file|they) (?:exists?|is there|is present)"
                       r"|if present|when present|where present|if any|optional (?:file|doc|notes|part)"
                       r"|(?:its|their|the repo's|the machine's) own (?:part|notes|section) of)\b", re.IGNORECASE)


def _deferred(text: _Text, repo: _Repo) -> list[Finding]:
    """A rule handed to a file that may not be there: a local file (`local/`, `*.local.*`), or a
    pointer said to hold only if the file exists. A rule is written once, in a file every
    machine has."""

    found = []
    for line in text.lines:
        if line.fenced:
            continue
        pointed = [named[0] for word, _, _ in _pointers(line) if (named := _path_of(word, repo))]
        local = [path for path in pointed if _LOCAL_PATH.search(path)]
        if local:
            found.append(Finding(text.rel, line.number, "rule_deferred",
                                 f"`{local[0]}` is a local file; write the rule where every machine has it"))
        elif (pointed or "CLAUDE.md" in line.text) and _OPTIONAL.search(line.text):
            found.append(Finding(text.rel, line.number, "rule_deferred",
                                 f"a rule left to a file that may not be there ({_OPTIONAL.search(line.text).group(0)!r})"))
    return found


def _skills(repo: Path) -> list[Finding]:
    """#397's check, reused: a skill named that is neither his copy nor ours."""

    from orch_it.skills.references import check as skill_check

    found = []
    for line in skill_check(repo):
        where, _, detail = line.partition(": ")
        path, _, number = where.partition(":")
        found.append(Finding(path, int(number) if number.isdigit() else 0, "skill_unknown", detail or line))
    return found


_NORMATIVE = re.compile(r"\b(?:must|never|always|only|do not|don't|may not|cannot|is refused|are refused|refuses)\b",
                        re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z`\"(*_])")
_WORD = re.compile(r"[a-z0-9][a-z0-9_]*")
_STOP = frozenset("""a an the and or but nor of to in on at by for with from into onto as is are was were be been
being it its this that these those there here which who whom whose what when where why how than then so if
else not no any each every all both either neither one two its their his her our your my we you they he she
i me us them do does did done has have had having can could would should will shall may might must never
always only also just very more most less least such same other another own per via about after before
between over under up down out off again once while until because though although yet still even""".split())
RECORDS = "docs/adr/"
RULE_WORDS_MIN = 5  # a shorter sentence is too general to be one rule said twice
RULE_LIKENESS = 0.6  # the share of their content words two sentences hold in common


def _stem(word: str) -> str:
    for ending in ("ing", "ed", "es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            word = word[: -len(ending)]
            break
    return word.removesuffix("e") if len(word) > 4 else word


@dataclass(frozen=True)
class _Rule:
    rel: str
    line: int
    words: frozenset[str]


def _rules(text: _Text) -> list[_Rule]:
    """The normative sentences of a text's prose (not fenced, not a heading or a table row), each
    with the line it starts on and its content words."""

    rules: list[_Rule] = []
    paragraph: list[_Line] = []
    skip = _frontmatter_end(text)

    def flush() -> None:
        if not paragraph:
            return
        joined, starts = "", []
        for line in paragraph:
            starts.append((len(joined), line.number))
            joined += line.text.strip().lstrip("-*0123456789.> ").strip() + " "
        at = 0
        for sentence in _SENTENCE_END.split(joined):
            begin = joined.find(sentence, at)
            at = begin + len(sentence)
            if not _NORMATIVE.search(sentence):
                continue
            words = frozenset(_stem(w) for w in _WORD.findall(sentence.lower().replace("`", "")) if w not in _STOP)
            if len(words) >= RULE_WORDS_MIN:
                number = max(n for offset, n in starts if offset <= begin)
                rules.append(_Rule(text.rel, number, words))
        paragraph.clear()

    for line in text.lines:
        stripped = line.text.strip()
        item = re.match(r"^\s*(?:[-*]|\d+\.)\s", line.text)
        if line.number <= skip or line.fenced or not stripped or _HEADING.match(stripped) or stripped.startswith("|"):
            flush()
            continue
        if item:
            flush()
        paragraph.append(line)
    flush()
    return rules


def _twice(rules: list[_Rule]) -> list[Finding]:
    """One finding per pair of normative sentences, in two files or two places of one, that hold
    most of their content words in common: one rule said twice (or two rules on one object that
    disagree), at the later place."""

    found = []
    ordered = sorted(rules, key=lambda r: (r.rel, r.line))
    for i, first in enumerate(ordered):
        for second in ordered[i + 1:]:
            if (first.rel, first.line) == (second.rel, second.line):
                continue
            common = len(first.words & second.words)
            if common / len(first.words | second.words) >= RULE_LIKENESS:
                found.append(Finding(second.rel, second.line, "rule_twice",
                                     f"the rule of {first.rel}:{first.line} said again; write it once and link to it"))
    return found


def check(repo: str | Path) -> list[Finding]:
    """Every finding of the repo's texts, sorted by file and line, each once."""

    repo = Path(repo)
    judge = _Repo(repo)
    _read_sections.cache_clear()
    found: set[Finding] = set(_skills(repo))
    rules: list[_Rule] = []
    for path in text_files(repo):
        rel = path.relative_to(repo).as_posix()
        try:
            content = path.read_bytes().decode("utf-8").removeprefix("﻿").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError):
            found.add(Finding(rel, 0, "text_unreadable", "not UTF-8 text"))
            continue
        text = _Text(rel, _lines(content))
        found.update(_paths(text, judge))
        found.update(_commands(text))
        found.update(_names(text))
        found.update(_literals(text))
        found.update(_deferred(text, judge))
        if not rel.startswith(RECORDS):  # an ADR records why a rule is so: it says the rule again by design
            rules += _rules(text)
    found.update(_twice(rules))
    return sorted(found)
