"""Skill copies (#397 decision 1): his skills, byte for byte from his upstream at one commit, in
`.claude/skills/<name>/`, and the manifest `.claude/skills/pocock.json` that says where each came
from.

A tree is the skills of one upstream commit: `{upstream path: {file path in the folder: bytes}}`,
read from the commit's blobs, never from a work tree (whose line endings git may have changed).
A skill is a folder under `skills/` holding a `SKILL.md`, outside `skills/deprecated/`; its name is
the folder's name. The manifest records the upstream repo and commit, and per skill its upstream
path and the git blob hash of every file: the copies (`skills`) and his skills left out
(`not_copied`, each with why), so a later commit's new skill is told from one that was always
there. A folder of `.claude/skills/` the manifest does not name is ours: never written, never
removed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

GIT_TIMEOUT_SECONDS = 120
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SKILLS_FOLDER = PurePosixPath(".claude/skills")
MANIFEST_NAME = "pocock.json"
SKILLS_ROOT = "skills/"
LEFT_OUT_GROUP = "skills/deprecated/"
SKILL_FILE = "SKILL.md"
ABOUT = ("His skills copied byte for byte from `upstream` at `commit` (#397 decision 1): per copy its upstream path"
         " and the git blob hash of every file; `not_copied` his skills left out and why. Written by"
         " `python -m orch_it skills update`; a skill folder this file does not name is ours.")

SKILLS_ERROR_CODES = (
    "git_failed",          # git missing, timed out or refused
    "commit_unknown",      # the commit is not in the git folder
    "no_skills",           # a tree with no skill: never read as every skill removed
    "name_clash",          # two upstream skill folders of one name
    "unsupported_entry",   # a link or submodule inside a skill folder
    "manifest_unreadable", # the manifest is no JSON
    "bad_manifest",        # the manifest is JSON of the wrong shape
    "copy_over_ours",      # a copy would overwrite a skill folder of ours
)

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
_BLOB = re.compile(r"[0-9a-f]{40}")

Tree = dict[str, dict[str, bytes]]


class SkillsError(RuntimeError):
    """`code` is one of `SKILLS_ERROR_CODES`."""

    def __init__(self, code: str, detail: str = ""):
        if code not in SKILLS_ERROR_CODES:
            raise ValueError(f"not a skills error code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def blob_hash(data: bytes) -> str:
    """The hash git gives these bytes as a blob, so the manifest compares with any upstream tree."""

    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def skill_name(path: str) -> str:
    return PurePosixPath(path).name


def _git(git_dir: Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=git_dir, input=stdin, capture_output=True,
                              timeout=GIT_TIMEOUT_SECONDS, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SkillsError("git_failed", f"git {args[0]}: {e}") from None


def read_tree(git_dir: str | Path, commit: str) -> Tree:
    """The skills of `commit` in the git folder `git_dir`, read from its blobs."""

    git_dir = Path(git_dir)
    if not _git(git_dir, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0:
        raise SkillsError("commit_unknown", commit)
    listed = _git(git_dir, "ls-tree", "-r", "-z", commit, "--", SKILLS_ROOT)
    if listed.returncode != 0:
        raise SkillsError("git_failed", listed.stderr.decode("utf-8", "replace").strip())
    entries = []  # (mode, type, sha, path)
    for item in listed.stdout.split(b"\0"):
        if item:
            meta, _, path = item.partition(b"\t")
            mode, kind, sha = meta.decode().split()
            entries.append((mode, kind, sha, path.decode("utf-8")))
    folders = sorted(str(PurePosixPath(path).parent) for _, _, _, path in entries
                     if PurePosixPath(path).name == SKILL_FILE and not path.startswith(LEFT_OUT_GROUP))
    # a folder inside another skill's folder is part of that skill, not a skill of its own
    folders = [f for f in folders if not any(f.startswith(other + "/") for other in folders)]
    if not folders:
        raise SkillsError("no_skills", commit)
    names: dict[str, str] = {}
    for folder in folders:
        if skill_name(folder) in names:
            raise SkillsError("name_clash", f"{names[skill_name(folder)]} and {folder}")
        names[skill_name(folder)] = folder

    wanted: list[tuple[str, str, str]] = []  # (folder, file path in folder, sha)
    for mode, kind, sha, path in entries:
        folder = next((f for f in folders if path.startswith(f + "/")), None)
        if folder is None:
            continue
        if kind != "blob" or mode == "120000":
            raise SkillsError("unsupported_entry", path)
        wanted.append((folder, path[len(folder) + 1:], sha))
    blobs = _cat_blobs(git_dir, sorted({sha for _, _, sha in wanted}))
    tree: Tree = {folder: {} for folder in folders}
    for folder, rel, sha in wanted:
        tree[folder][rel] = blobs[sha]
    return tree


def _cat_blobs(git_dir: Path, shas: list[str]) -> dict[str, bytes]:
    done = _git(git_dir, "cat-file", "--batch", stdin="".join(f"{sha}\n" for sha in shas).encode())
    if done.returncode != 0:
        raise SkillsError("git_failed", done.stderr.decode("utf-8", "replace").strip())
    out, at, blobs = done.stdout, 0, {}
    for sha in shas:
        end = out.index(b"\n", at)
        header = out[at:end].decode().split()
        if len(header) != 3 or header[0] != sha or header[1] != "blob":
            raise SkillsError("git_failed", f"cat-file: {out[at:end]!r}")
        size = int(header[2])
        blobs[sha] = out[end + 1:end + 1 + size]
        at = end + 1 + size + 1
    return blobs


def _files(skill: dict[str, bytes]) -> dict[str, str]:
    return {rel: blob_hash(data) for rel, data in sorted(skill.items())}


@dataclass
class Manifest:
    upstream: str
    commit: str
    skills: dict[str, dict]       # name -> {"path": upstream path, "files": {file: blob hash}}
    not_copied: dict[str, dict]   # name -> {"path", "why", "files"}

    @classmethod
    def build(cls, tree: Tree, *, upstream: str, commit: str, not_copied: dict[str, str]) -> Manifest:
        """Every skill of `tree` is copied but the ones `not_copied` names (name -> why); a name
        there that the tree no longer has is dropped."""

        skills, left_out = {}, {}
        for path in sorted(tree, key=skill_name):
            name = skill_name(path)
            entry = {"path": path, "files": _files(tree[path])}
            if name in not_copied:
                left_out[name] = {"path": path, "why": not_copied[name], "files": entry["files"]}
            else:
                skills[name] = entry
        return cls(upstream, commit, skills, left_out)

    @staticmethod
    def path(repo: str | Path) -> Path:
        return Path(repo) / SKILLS_FOLDER / MANIFEST_NAME

    @classmethod
    def load(cls, repo: str | Path) -> Manifest | None:
        """The repo's manifest, or None when it has none."""

        path = cls.path(repo)
        try:
            data = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            raise SkillsError("manifest_unreadable", f"{MANIFEST_NAME}: {e}") from None
        problem = _shape_problem(data)
        if problem:
            raise SkillsError("bad_manifest", problem)
        return cls(data["upstream"], data["commit"], data["skills"], data["not_copied"])

    def text(self) -> str:
        data = {"_about": ABOUT, "upstream": self.upstream, "commit": self.commit,
                "skills": self.skills, "not_copied": self.not_copied}
        return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def _shape_problem(data) -> str | None:
    if not isinstance(data, dict):
        return "not an object"
    for key in ("upstream", "commit"):
        if not isinstance(data.get(key), str) or not data[key]:
            return f"{key}: no text"
    for group, extra in (("skills", ()), ("not_copied", ("why",))):
        entries = data.get(group)
        if not isinstance(entries, dict):
            return f"{group}: not an object"
        for name, entry in entries.items():
            if not _NAME.fullmatch(name):
                return f"{group}.{name}: not a skill name"
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                return f"{group}.{name}: no path"
            if any(not isinstance(entry.get(key), str) for key in extra):
                return f"{group}.{name}: no why"
            files = entry.get("files")
            if not isinstance(files, dict):
                return f"{group}.{name}: no files"
            for rel, sha in files.items():
                if not _safe_rel(rel) or not isinstance(sha, str) or not _BLOB.fullmatch(sha):
                    return f"{group}.{name}.files.{rel}: bad file"
    if set(data["skills"]) & set(data["not_copied"]):
        return "a skill both copied and not copied"
    return None


def _safe_rel(rel) -> bool:
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        return False
    parts = rel.split("/")
    return all(part and part not in (".", "..") and ":" not in part for part in parts)


def _replace_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write(repo: str | Path, tree: Tree, manifest: Manifest) -> None:
    """Make `.claude/skills/` hold exactly the copies `manifest` names, from `tree`: each file
    written whole, files the copy no longer has removed, the folders of copies the old manifest
    named and this one drops removed; folders of ours untouched. The manifest is written last, so
    a write cut part-way leaves the old manifest and `verify` names what differs."""

    repo = Path(repo)
    root = repo / SKILLS_FOLDER
    old = Manifest.load(repo)
    old_names = set(old.skills) if old else set()
    by_name = {skill_name(path): path for path in tree}
    for name in manifest.skills:
        if name not in old_names and (root / name).exists():
            raise SkillsError("copy_over_ours", name)
    for name, entry in manifest.skills.items():
        folder, files = root / name, tree[by_name[name]]
        for rel, data in files.items():
            target = folder / rel
            if not target.is_file() or target.read_bytes() != data:
                _replace_bytes(target, data)
        for path in sorted(folder.rglob("*"), reverse=True):
            rel = path.relative_to(folder).as_posix()
            if path.is_file() and rel not in files:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    for name in sorted(old_names - set(manifest.skills)):
        shutil.rmtree(root / name, ignore_errors=True)
    _replace_bytes(Manifest.path(repo), manifest.text().encode("utf-8"))


def verify(repo: str | Path) -> list[str]:
    """Each difference between the copies on disk and the manifest, one line each, sorted: a file
    missing, a hash that differs, a file the manifest does not name, a copy folder missing, a
    skill left out whose folder is present. Empty when every copy is as recorded."""

    repo = Path(repo)
    root = repo / SKILLS_FOLDER
    manifest = Manifest.load(repo)
    if manifest is None:
        return [f"{MANIFEST_NAME}: missing"]
    problems = []
    for name, entry in manifest.skills.items():
        folder = root / name
        if not folder.is_dir():
            problems.append(f"{name}: copy missing")
            continue
        for rel, sha in entry["files"].items():
            path = folder / rel
            if not path.is_file():
                problems.append(f"{name}/{rel}: missing")
            elif blob_hash(path.read_bytes()) != sha:
                problems.append(f"{name}/{rel}: hash differs")
        for path in folder.rglob("*"):
            rel = path.relative_to(folder).as_posix()
            if path.is_file() and rel not in entry["files"]:
                problems.append(f"{name}/{rel}: not in the manifest")
    for name in manifest.not_copied:
        if (root / name).exists():
            problems.append(f"{name}: not copied, yet present")
    return sorted(problems)
