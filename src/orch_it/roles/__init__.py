"""The roles of the layer and their settings (#399 decision 5).

`roles.json` beside this module is the roles settings: per role its `model` and `effort`, and the
`fallback` model a launcher takes when the model's safeguard pauses a session (the brain's answer
on #399: a fallback per role, Opus 4.8 for the Opus roles). Like the label set (`orch_it.labels`)
it ships with orch-it, so every repo gets the same roles; a test or a person passes another file
with `RoleSet.load(path)` or `roles render --settings`.

A model is a full model id (`claude-...`), never an alias such as `sonnet`, so its price row can
be checked (`orch_it.events.pricing.missing_rates`). `effort` is one of Claude Code's levels, or
absent for the model's own default.

`render` writes each agent file's `model:` and `effort:` frontmatter lines from the settings and
leaves every other byte of the file as it was; `drift` names each agent file whose frontmatter
differs from the settings, or that names no role of them. A role with no agent file yet (the
brain's, until the owner merges it) is no drift.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ROLES_FILE = Path(__file__).with_name("roles.json")
AGENTS_FOLDER = Path(".claude") / "agents"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
ROLE_KEYS = ("model", "effort", "fallback")
ERROR_CODES = ("settings_unreadable", "bad_setting", "frontmatter_missing")
_ROLE = re.compile(r"[a-z][a-z0-9-]{0,63}")
_MODEL = re.compile(r"claude-[a-z0-9][a-z0-9.-]{0,63}")
_FRONT_LINE = {"model": re.compile(r"model[ \t]*:"), "effort": re.compile(r"effort[ \t]*:")}


class RolesError(ValueError):
    """`code`: `settings_unreadable` (no JSON object), `bad_setting` (`field` names the part, such
    as `roles.worker.model`), `frontmatter_missing` (an agent file `render` cannot write: `field`
    is its path)."""

    def __init__(self, code: str, field: str = ""):
        if code not in ERROR_CODES:
            raise ValueError(f"not a roles error code: {code}")
        self.code = code
        self.field = field
        super().__init__(f"{code}: {field}" if field else code)


@dataclass(frozen=True)
class Role:
    name: str
    model: str
    effort: str | None
    fallback: str


@dataclass(frozen=True)
class RoleSet:
    roles: dict[str, Role]

    @classmethod
    def load(cls, path: str | Path | None = None) -> RoleSet:
        path = Path(path) if path is not None else ROLES_FILE
        try:
            raw = json.loads(path.read_bytes().decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, ValueError) as e:
            raise RolesError("settings_unreadable", f"{path}: {e}") from None
        if not isinstance(raw, dict):
            raise RolesError("settings_unreadable", f"{path}: not an object")
        roles = raw.get("roles")
        if not isinstance(roles, dict) or not roles:
            raise RolesError("bad_setting", "roles")
        return cls({name: _role(name, value) for name, value in roles.items()})


def _role(name: object, value: object) -> Role:
    where = f"roles.{name}"
    if not isinstance(name, str) or not _ROLE.fullmatch(name) or not isinstance(value, dict):
        raise RolesError("bad_setting", where)
    for key in value:
        if key not in ROLE_KEYS:
            raise RolesError("bad_setting", f"{where}.{key}")
    model, effort, fallback = value.get("model"), value.get("effort"), value.get("fallback")
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise RolesError("bad_setting", f"{where}.model")
    if effort is not None and effort not in EFFORTS:
        raise RolesError("bad_setting", f"{where}.effort")
    if not isinstance(fallback, str) or not _MODEL.fullmatch(fallback) or fallback == model:
        raise RolesError("bad_setting", f"{where}.fallback")
    return Role(name, model, effort, fallback)


def _split(text: str) -> tuple[list[str], list[str]] | None:
    """(frontmatter lines, the rest) of an agent file, or None when it opens with no
    frontmatter closed by `---`."""

    lines = text.split("\n")
    if not lines or lines[0].removeprefix("﻿").rstrip("\r") != "---":
        return None
    for end in range(1, len(lines)):
        if lines[end].rstrip("\r") == "---":
            return lines[1:end], lines[end:]
    return None


def _value(front: list[str], key: str) -> str | None:
    for line in front:
        if _FRONT_LINE[key].match(line):
            return line.split(":", 1)[1].split("#", 1)[0].strip().strip("\"'") or None
    return None


def _rendered(front: list[str], role: Role, end: str) -> list[str]:
    """The frontmatter with its `model:` and `effort:` lines as the settings say, each in the
    place it had (a new one after `model:`, else after `name:`, else at the end); `end` is the file's `\\r` or
    nothing, so a CRLF file stays CRLF."""

    wanted = {"model": role.model, "effort": role.effort}
    out: list[str] = []
    for line in front:
        key = next((k for k, pattern in _FRONT_LINE.items() if pattern.match(line)), None)
        if key is None:
            out.append(line)
        elif wanted.get(key) is not None:
            out.append(f"{key}: {wanted.pop(key)}{end}")
        else:
            wanted.pop(key, None)  # a second line of the key, or an effort the settings leave out
    missing = [f"{key}: {value}{end}" for key, value in wanted.items() if value is not None]
    after = next((i + 1 for i, line in enumerate(out) if _FRONT_LINE["model"].match(line)), None)
    if after is None:
        after = next((i + 1 for i, line in enumerate(out) if line.startswith("name:")), len(out))
    return out[:after] + missing + out[after:]


def _agent_files(work_tree: Path) -> list[Path]:
    folder = work_tree / AGENTS_FOLDER
    return sorted(folder.glob("*.md")) if folder.is_dir() else []


def drift(work_tree: str | Path, roles: RoleSet) -> list[str]:
    """One line per agent file of `work_tree` out of step with `roles`."""

    work_tree = Path(work_tree)
    found = []
    for path in _agent_files(work_tree):
        rel = path.relative_to(work_tree).as_posix()
        role = roles.roles.get(path.stem)
        if role is None:
            found.append(f"{rel}: no role `{path.stem}` in the roles settings")
            continue
        parts = _split(path.read_bytes().decode("utf-8", "replace"))
        if parts is None:
            found.append(f"{rel}: no frontmatter")
            continue
        for key, wanted in (("model", role.model), ("effort", role.effort)):
            have = _value(parts[0], key)
            if have != wanted:
                found.append(f"{rel}: {key} is {have or 'not set'}, the roles settings say {wanted or 'not set'}")
    return found


def render(work_tree: str | Path, roles: RoleSet) -> list[tuple[str, str]]:
    """Write the `model:` and `effort:` lines of every agent file of a role from `roles`;
    (path, `changed` or `same`) per file. Nothing is written when any file of a role has no
    frontmatter; a file of no role is left alone."""

    work_tree = Path(work_tree)
    planned = []
    for path in _agent_files(work_tree):
        role = roles.roles.get(path.stem)
        if role is None:
            continue
        text = path.read_bytes().decode("utf-8")
        parts = _split(text)
        if parts is None:
            raise RolesError("frontmatter_missing", path.relative_to(work_tree).as_posix())
        front, rest = parts
        first = text.split("\n", 1)[0]
        end = "\r" if first.endswith("\r") else ""
        planned.append((path, text, "\n".join([first, *_rendered(front, role, end), *rest])))
    done = []
    for path, before, after in planned:
        rel = path.relative_to(work_tree).as_posix()
        if after == before:
            done.append((rel, "same"))
            continue
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(after.encode("utf-8"))
        os.replace(temporary, path)
        done.append((rel, "changed"))
    return done
