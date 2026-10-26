"""A personal skill of the same name as a repo skill (#397 decision 3).

Claude Code loads a personal skill over a project skill of the same name, so a skill in the user's
own folder (`$CLAUDE_CONFIG_DIR/skills`, default `~/.claude/skills`) silently replaces the repo's
copy or ours. At session start a hook compares the two folders' skill names and writes one
`skill.shadowed` event per clash; it never blocks the session and prints nothing into it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from orch_it.events.store import Store
from orch_it.skills.copies import SKILL_FILE, SKILLS_FOLDER, Manifest, SkillsError


@dataclass(frozen=True)
class Clash:
    skill: str
    copy: bool        # his copy (in the manifest), else a skill of ours
    same_text: bool   # the personal SKILL.md is byte for byte the repo's


def personal_folder() -> Path:
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(config) if config else Path.home() / ".claude") / "skills"


def _skills(folder: Path) -> dict[str, Path]:
    try:
        return {path.name: path / SKILL_FILE for path in folder.iterdir() if (path / SKILL_FILE).is_file()}
    except OSError:  # no folder, or one we may not read: no skills
        return {}


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def clashes(repo: Path, personal: Path) -> list[Clash]:
    """The repo's skills that a personal skill of the same name shadows, sorted by name."""

    ours = _skills(repo / SKILLS_FOLDER)
    theirs = _skills(personal)
    try:
        manifest = Manifest.load(repo)
    except SkillsError:
        manifest = None
    copied = set(manifest.skills) if manifest else set()
    return [Clash(name, name in copied, _read(ours[name]) == _read(theirs[name]))
            for name in sorted(ours.keys() & theirs.keys())]


def write_shadowed(store: Store, *, repo: Path, personal: Path, repo_name: str,
                   session: str | None) -> list[Clash]:
    found = clashes(repo, personal)
    if found:
        with store.writer() as writer:
            for clash in found:
                writer.write({
                    "kind": "skill.shadowed", "repo": repo_name, "ticket": None, "role": "hook",
                    "session": session, "parent_session": None, "turn": None, "step": None, "run": None,
                    "note": None,
                    "detail": {"skill": clash.skill, "copy": clash.copy, "same_text": clash.same_text}})
    return found
