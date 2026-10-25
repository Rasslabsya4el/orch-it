"""The real path a command would act on, and whether it is a protected object (#395 decision 2).

A spelled path becomes the real path on disk: joined to the directory the command runs in,
MSYS `/c/...` and WSL `/mnt/c/...` read as drive paths only on Windows, every link (symlink,
junction) on the way followed, each other name kept as spelled. Two names are compared by the
case rule of the folder that holds them, measured there (do two spellings reach one entry?),
never by the OS name and never by lower-casing.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import stat
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

# a chain of links longer than this is a loop: the path stops being followed there
MAX_LINK_HOPS = 64

_MSYS = re.compile(r"^/(?:cygdrive/)?([A-Za-z])(?=/|$)")
_WSL = re.compile(r"^/mnt/([A-Za-z])(?=/|$)")
_VERBATIM = re.compile(r"^[\\/]{2}[?.][\\/](UNC[\\/])?", re.IGNORECASE)


def resolve(spelled: str, cwd: str | None, *, windows: bool,
            posix_root: Callable[[str], str | None] | None = None) -> str | None:
    """The real path `spelled` names when a command running in `cwd` uses it, or None when it
    cannot be known (a relative path and no known directory, an empty word).

    Windows paths (`windows`): MSYS `/c/...`, `/cygdrive/c/...` and WSL `/mnt/c/...` are drive
    paths; another absolute POSIX path (Git Bash's `/tmp`) goes through `posix_root` when given;
    `..` is read on the spelling, as Windows does, and each name loses its trailing dots and
    spaces; an 8.3 short name becomes its long name. POSIX paths: `..` is read on the real
    path, as the kernel does. Either way every link on the way is followed."""

    if not spelled:
        return None
    flavour = ntpath if windows else posixpath
    path = _native(spelled, windows, posix_root)
    if cwd is not None:
        path = flavour.join(_native(cwd, windows, posix_root), path)
    if windows:
        drive, rest = ntpath.splitdrive(path)
        if not drive or not rest.startswith(("\\", "/")):
            return None
        path = ntpath.normpath(path)
        drive, rest = ntpath.splitdrive(path)
        path = drive + "\\".join(part.rstrip(" .") if part not in ("", ".", "..") else part
                                 for part in rest.split("\\"))
    elif not path.startswith("/"):
        return None
    return _follow(path, flavour, windows)


def _native(path: str, windows: bool, posix_root) -> str:
    if not windows:
        return path
    path = _VERBATIM.sub(lambda m: "\\\\" if m.group(1) else "", path)
    found = _MSYS.match(path) or _WSL.match(path)
    if found:
        return f"{found.group(1)}:\\" + path[found.end():].lstrip("/")
    if path.startswith("/") and not path.startswith("//") and posix_root is not None:
        return posix_root(path) or path
    return path


def _split(path: str, flavour) -> tuple[str, list[str]]:
    drive, rest = flavour.splitdrive(path)
    separators = "\\/" if flavour is ntpath else "/"
    root = rest[:len(rest) - len(rest.lstrip(separators))]
    anchor = drive + (flavour.sep if root else "")
    return anchor, [part for part in re.split(r"[\\/]" if flavour is ntpath else "/", rest) if part]


def _follow(path: str, flavour, windows: bool) -> str:
    anchor, parts = _split(path, flavour)
    pending = deque(parts)
    current = anchor
    hops = 0
    while pending:
        part = pending.popleft()
        if part == ".":
            continue
        if part == "..":
            if current != anchor:
                current = flavour.dirname(current)
            continue
        candidate = flavour.join(current, part)
        if windows and "~" in part:
            candidate = flavour.join(current, _long_name(candidate) or part)
        target = _link_target(candidate, windows) if hops < MAX_LINK_HOPS else None
        if target is None:
            current = candidate
            continue
        hops += 1
        target = flavour.join(current, _native(target, windows, None))
        if windows:
            target = ntpath.normpath(target)
        anchor, parts = _split(target, flavour)
        current = anchor
        pending.extendleft(reversed(parts))
    return current


def _link_target(path: str, windows: bool) -> str | None:
    """Where the link at `path` points, or None when `path` is no link (or not there)."""

    if windows != (sys.platform == "win32"):
        return None  # a path of the other OS names nothing on this disk
    try:
        mode = os.lstat(path)
    except (OSError, ValueError):
        return None
    junction = windows and getattr(mode, "st_reparse_tag", 0) == stat.IO_REPARSE_TAG_MOUNT_POINT
    if not (stat.S_ISLNK(mode.st_mode) or junction):
        return None
    try:
        return os.readlink(path)
    except (OSError, ValueError):
        return None


def _long_name(path: str) -> str | None:
    """The long name of the last part of an existing Windows path that may be an 8.3 name."""

    if sys.platform != "win32":
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetLongPathNameW(path, buffer, len(buffer)):
        return None
    return ntpath.basename(buffer.value) or None


def measure_case_rule(folder: str) -> bool:
    """True when the folder's names are case-insensitive: an entry's name with its letters'
    case swapped reaches the same entry. Measured on the first entry with a cased letter, else
    on the folder's own name in its parent, else on the nearest ancestor that can be measured;
    a folder nothing can be measured on counts as insensitive (more names compare equal, so
    more is protected)."""

    current = os.path.abspath(folder)
    while True:
        found = _measure_on_entries(current)
        if found is not None:
            return found
        parent = os.path.dirname(current)
        name = os.path.basename(current)
        if parent and parent != current:
            found = _same_entry_swapped(parent, name)
            if found is not None:
                return found
            current = parent
            continue
        return True


def _measure_on_entries(folder: str) -> bool | None:
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                found = _same_entry_swapped(folder, entry.name)
                if found is not None:
                    return found
    except OSError:
        return None
    return None


def _same_entry_swapped(folder: str, name: str) -> bool | None:
    """Whether `name` with its case swapped is the same entry of `folder`; None when the name
    has no cased letter or the entry itself cannot be read."""

    swapped = name.swapcase()
    if swapped == name:
        return None
    try:
        original = os.lstat(os.path.join(folder, name))
    except OSError:
        return None
    try:
        other = os.lstat(os.path.join(folder, swapped))
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return os.path.samestat(original, other)


class GuardError(RuntimeError):
    """A fact the guard needs and could not read. `code`: `git_unreadable`."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


GIT_SECONDS = 15
_NOT_A_REPO = "not a git repository"


def protected_roots(cwd: str, *, windows: bool) -> tuple[str, ...]:
    """Every worktree of the repo around `cwd` (`git worktree list --porcelain`, the main
    checkout included, wherever each sits on disk) and the repo's common `.git` folder, each
    as its real path; `()` outside a repo. A git that cannot answer raises `GuardError`."""

    listing = _git(cwd, "worktree", "list", "--porcelain")
    if listing is None:
        return ()
    spelled = [line[len("worktree "):] for line in listing.splitlines() if line.startswith("worktree ")]
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        spelled.append(common.strip())
    roots = (resolve(path, cwd, windows=windows) for path in spelled if path)
    return tuple(dict.fromkeys(root for root in roots if root))


def _git(cwd: str, *args: str) -> str | None:
    import subprocess

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GIT_SECONDS,
                              stdin=subprocess.DEVNULL, creationflags=flags)
    except FileNotFoundError:
        if not os.path.isdir(cwd):
            return None  # a directory that is not there holds no repo
        raise GuardError("git_unreadable", "git not found") from None
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GuardError("git_unreadable", f"{args[0]}: {e}") from None
    if done.returncode != 0:
        if _NOT_A_REPO in done.stderr:
            return None
        raise GuardError("git_unreadable", f"{args[0]}: rc {done.returncode}")
    return done.stdout


@dataclass(frozen=True)
class Finding:
    """Why a target is protected. `rule`: `protected_root` (it is one), `ancestor_of_root`
    (one lies below it), `link_outward` (its tree holds a link leading outside it); `path`:
    the protected root, or the link found."""

    rule: str
    path: str


CaseRule = Callable[[str], bool]


def judge(target: str | None, roots, *, windows: bool, roots_too: bool = True,
          walk_links: bool = True, case_rule: CaseRule = measure_case_rule) -> Finding | None:
    """Whether deleting (or moving) the real path `target` reaches a protected object: None
    when it does not, or it is not there. `roots_too` False lets a root itself through (a
    filtered `find` deletes only what matches below it); `walk_links` False skips the walk
    (a move goes through no link). `case_rule(folder)` says whether names in `folder` are
    case-insensitive; it is measured once per folder per call."""

    if target is None or not os.path.lexists(target):
        return None
    flavour = ntpath if windows else posixpath
    insensitive = _cached(case_rule)
    for root in roots:
        relation = _relation(target, root, flavour, insensitive)
        if relation == "same" and roots_too:
            return Finding("protected_root", root)
        if relation == "ancestor":
            return Finding("ancestor_of_root", root)
    if walk_links and os.path.isdir(target):
        link = _outward_link(target, flavour, windows, insensitive)
        if link is not None:
            return Finding("link_outward", link)
    return None


def _cached(case_rule: CaseRule) -> CaseRule:
    seen: dict[str, bool] = {}

    def insensitive(folder: str) -> bool:
        if folder not in seen:
            seen[folder] = case_rule(folder)
        return seen[folder]

    return insensitive


def _relation(path: str, other: str, flavour, insensitive: CaseRule) -> str | None:
    """`same` when the two real paths name one entry, `ancestor` when `other` lies below
    `path`, else None; each pair of names compared by the rule of the folder holding them."""

    anchor, parts = _split(path, flavour)
    other_anchor, other_parts = _split(other, flavour)
    if flavour is ntpath:
        if anchor.casefold() != other_anchor.casefold():  # a drive letter is no folder entry
            return None
    elif anchor != other_anchor:
        return None
    if len(parts) > len(other_parts):
        return None
    folder = anchor
    for name, other_name in zip(parts, other_parts):
        if name != other_name and not (name.casefold() == other_name.casefold() and insensitive(folder)):
            return None
        folder = flavour.join(folder, name)
    return "same" if len(parts) == len(other_parts) else "ancestor"


def _outward_link(folder: str, flavour, windows: bool, insensitive: CaseRule) -> str | None:
    """The first link in `folder`'s tree whose real target lies outside the tree; the walk
    has no entry cap and descends into no link."""

    stack = [folder]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                listed = list(entries)
        except OSError:
            continue  # what cannot be read cannot be deleted through either
        for entry in listed:
            try:
                is_link = entry.is_symlink() or entry.is_junction()
                if not is_link:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    continue
            except OSError:
                continue
            target = resolve(entry.path, None, windows=windows)
            if target is None or _relation(folder, target, flavour, insensitive) is None:
                return entry.path
    return None
