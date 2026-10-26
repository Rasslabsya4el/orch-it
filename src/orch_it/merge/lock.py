"""The repo lock of the merge line (#400 decision 2; row 7): one holder at a time per repo.

The lock is a folder `<data folder>/merge-lock/<owner>__<name>` (lower case: GitHub's names are the
same whatever their case), outside every repo root. It is taken by creating that folder, which the
OS does atomically for one caller only, and its file `owner` says who holds it (`holder`, `since`,
`pid`, `machine`, a random `token`). `hold` releases it on exit, also when the body raises; a
release removes only a lock whose token is its own, so a line that ends late never removes the
lock of the line after it.

A second `hold` waits, polling every `poll_seconds`, tells `on_wait` once who holds it, and raises
`LockError("lock_busy")` at `lock_wait_minutes`. A lock older than `lock_stale_minutes` (its
holder died, or its machine slept holding it) is reported at once with `LockError("lock_stale")`
and never taken: only `unlock_stale`, run on purpose, removes it. A folder whose owner file is not
written yet, or cannot be read, is held by `unknown` since the folder's own time.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from orch_it import settings as installation
from orch_it.events.store import machine_id
from orch_it.merge.settings import MergeSettings

FOLDER = "merge-lock"
OWNER_FILE = "owner"
LOCK_ERROR_CODES = ("lock_busy", "lock_stale", "lock_not_stale", "lock_missing", "lock_unwritable", "bad_repo")
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.\.?$)[A-Za-z0-9_.-]{1,100}")


@dataclass(frozen=True)
class Holder:
    holder: str
    since: datetime | None
    pid: int | None = None
    machine: str | None = None
    token: str | None = None

    def said(self) -> str:
        since = self.since.isoformat(timespec="seconds") if self.since else "an unknown time"
        pid = f", pid {self.pid}" if self.pid else ""
        return f"{self.holder} since {since}{pid}"


class LockError(Exception):
    """`code`: `lock_busy` (held past the wait bound), `lock_stale` (older than its stale bound:
    reported, not taken), `lock_not_stale`, `lock_missing` (for `unlock_stale`), `lock_unwritable`
    (the lock folder cannot be made), `bad_repo` (no `owner/name`). `holder`: who holds it."""

    def __init__(self, code: str, detail: str = "", holder: Holder | None = None):
        if code not in LOCK_ERROR_CODES:
            raise ValueError(f"not a lock error code: {code}")
        self.code = code
        self.holder = holder
        super().__init__(f"{code}: {detail}" if detail else code)


def lock_folder(repo: str, *, root: Path | None = None) -> Path:
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise LockError("bad_repo", repr(repo)[:80])
    owner, name = repo.lower().split("/")
    return (root or installation.data_folder() / FOLDER) / f"{owner}__{name}"


def _now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def hold(repo: str, holder: str, *, settings: MergeSettings, root: Path | None = None,
         clock: Callable[[], datetime] = _now, sleep: Callable[[float], None] = time.sleep,
         on_wait: Callable[[Holder], None] | None = None) -> Iterator[Holder]:
    """Hold the repo's lock for the body of the `with`; see the module text for the waits."""

    folder = lock_folder(repo, root=root)
    started, told = clock(), False
    while not _take(folder):
        found = holder_of(repo, root=root)
        if found is None:
            continue  # released between the try and the read: try again at once
        if _age_minutes(found, clock) > settings.lock_stale_minutes:
            raise LockError("lock_stale", f"{repo} held by {found.said()}", found)
        if not told and on_wait is not None:
            on_wait(found)
            told = True
        waited = (clock() - started).total_seconds()
        bound = settings.lock_wait_minutes * 60
        if waited >= bound:
            raise LockError("lock_busy", f"{repo} held by {found.said()}", found)
        sleep(min(settings.poll_seconds, bound - waited))
    mine = Holder(holder, clock(), os.getpid(), machine_id()[:12], uuid.uuid4().hex)
    try:
        _write_owner(folder, mine)
    except OSError as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise LockError("lock_unwritable", f"{folder}: {e}") from None
    try:
        yield mine
    finally:
        _release(folder, mine.token)


def holder_of(repo: str, *, root: Path | None = None) -> Holder | None:
    """Who holds the repo's lock, None when nobody does."""

    folder = lock_folder(repo, root=root)
    try:
        taken = datetime.fromtimestamp(folder.stat().st_mtime, UTC)
    except FileNotFoundError:
        return None
    try:
        said = json.loads((folder / OWNER_FILE).read_text("utf-8"))
        since = datetime.fromisoformat(said["since"])
        return Holder(str(said["holder"]), since if since.tzinfo else since.replace(tzinfo=UTC),
                      said.get("pid") if isinstance(said.get("pid"), int) else None,
                      said.get("machine") if isinstance(said.get("machine"), str) else None,
                      said.get("token") if isinstance(said.get("token"), str) else None)
    except (OSError, ValueError, KeyError, TypeError):
        return Holder("unknown", taken)


def unlock_stale(repo: str, *, settings: MergeSettings, root: Path | None = None,
                 clock: Callable[[], datetime] = _now) -> Holder:
    """Remove the repo's lock, only when it is stale; returns who held it."""

    found = holder_of(repo, root=root)
    if found is None:
        raise LockError("lock_missing", repo)
    if _age_minutes(found, clock) <= settings.lock_stale_minutes:
        raise LockError("lock_not_stale", f"{repo} held by {found.said()}", found)
    shutil.rmtree(lock_folder(repo, root=root), ignore_errors=True)
    return found


def _take(folder: Path) -> bool:
    try:
        folder.parent.mkdir(parents=True, exist_ok=True)
        folder.mkdir()
        return True
    except FileExistsError:
        return False
    except PermissionError:
        if folder.exists() or folder.parent.is_dir():
            return False  # Windows: a folder being removed by its holder at this moment
        raise LockError("lock_unwritable", str(folder)) from None
    except OSError as e:
        raise LockError("lock_unwritable", f"{folder}: {e}") from None


def _write_owner(folder: Path, mine: Holder) -> None:
    """The owner file written whole: a reader sees no file or the whole of it."""

    text = json.dumps({"holder": mine.holder, "since": mine.since.isoformat(), "pid": mine.pid,
                       "machine": mine.machine, "token": mine.token})
    temporary = folder.parent / f".{folder.name}.{mine.token}.tmp"
    temporary.write_text(text, "utf-8")
    os.replace(temporary, folder / OWNER_FILE)


def _release(folder: Path, token: str | None) -> None:
    try:
        said = json.loads((folder / OWNER_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(said, dict) or said.get("token") != token:
        return  # no longer ours: removed as stale and taken by another line
    shutil.rmtree(folder, ignore_errors=True)


def _age_minutes(found: Holder, clock: Callable[[], datetime]) -> float:
    if found.since is None:
        return 0.0
    return (clock() - found.since).total_seconds() / 60
