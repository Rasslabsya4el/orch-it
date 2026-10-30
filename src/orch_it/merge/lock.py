"""The repo lock of the merge line (#400 decision 2; row 7): one holder at a time per repo on
this machine (the line runs on the machine under the owner's `gh`; two machines running the line
on one repo do not see each other's lock).

The lock is a folder `<data folder>/merge-lock/<owner>__<name>` (lower case: GitHub's names are the
same whatever their case), outside every repo root. It is taken by creating that folder, which the
OS does atomically for one caller only, and its file `owner` says who holds it (`holder`, `since`,
`pid`, `machine`, the holder process's `started` time, a random `token`). `hold` releases it on exit, also when the body raises; a
release removes only a lock whose token is its own, so a line that ends late never removes the
lock of the line after it, and it removes the owner file last, so a lock it cannot remove whole
still names its holder.

A second `hold` waits, polling every `poll_seconds`, tells `on_wait` once who holds it, and raises
`LockError("lock_busy")` at `lock_wait_minutes`. A lock older than `lock_stale_minutes` (its
holder died, or its machine slept holding it), or one whose `since` lies in the future, is
reported at once with `LockError("lock_stale")` and never taken: only `unlock_stale`, run on
purpose, removes it. A folder whose owner file is not written yet, or cannot be read, is held by
`unknown` since the folder's own time.

A lock whose holder ran on this machine is dead when its pid no longer exists, or when the process
now holding that pid started at another time than the owner file's `started` (pid reuse); such a
lock is taken at once, with no wait and no stale age: the `with` yields the new `Holder`, whose
`taken_from` names the dead holder (pid, machine). A lock that names another machine is never
judged by its pid, a live holder stays busy, and an owner file with no `started` (older files) is
judged by its pid alone.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil

from orch_it import settings as installation
from orch_it.events.store import machine_id
from orch_it.merge.settings import MergeSettings

FOLDER = "merge-lock"
OWNER_FILE = "owner"
LOCK_ERROR_CODES = ("lock_busy", "lock_stale", "lock_not_stale", "lock_missing", "lock_unwritable", "bad_repo")
CLOCK_SKEW = timedelta(minutes=5)  # a `since` further ahead than this is no time a holder took it
START_TOLERANCE = 0.5  # seconds: a holder's start time read back from the owner file equals the live one to this
UNKNOWN = "unknown"  # the holder of a lock whose owner file is not written yet, or unreadable
WRITE_GRACE_MINUTES = 1  # so long a waiter gives a fresh lock to name its holder before it says `unknown`
RELEASE_TRIES = 5  # a file held open for a moment by another program (Windows) is tried again
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.\.?$)[A-Za-z0-9_.-]{0,99}[A-Za-z0-9_-]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TAKEN, _HELD, _DENIED = "taken", "held", "denied"


@dataclass(frozen=True)
class Holder:
    holder: str
    since: datetime
    pid: int | None = None
    machine: str | None = None
    token: str | None = None
    started: float | None = None  # the holder process's start time (psutil create_time), None in older files
    taken_from: Holder | None = None  # set only on the Holder `hold` yields: the dead holder this lock took over

    def age_minutes(self, now: datetime) -> float:
        """Minutes since it was taken; a `since` in the future is no age at all: infinite."""

        if self.since - now > CLOCK_SKEW:
            return float("inf")
        return max(0.0, (now - self.since).total_seconds() / 60)

    def said(self) -> str:
        pid = f", pid {self.pid}" if self.pid else ""
        return f"{_CONTROL.sub(' ', self.holder)[:200]} since {self.since.isoformat(timespec='seconds')}{pid}"


class LockError(Exception):
    """`code`: `lock_busy` (held past the wait bound), `lock_stale` (older than its stale bound:
    reported, not taken), `lock_not_stale`, `lock_missing` (for `unlock_stale`), `lock_unwritable`
    (the lock folder cannot be made or removed), `bad_repo` (no `owner/name`). `holder`: who holds it."""

    def __init__(self, code: str, detail: str = "", holder: Holder | None = None):
        if code not in LOCK_ERROR_CODES:
            raise ValueError(f"not a lock error code: {code}")
        self.code = code
        self.holder = holder
        super().__init__(f"{code}: {detail}" if detail else code)


def lock_folder(repo: str, *, root: Path | None = None) -> Path:
    """The lock folder of `repo` under `root`, the folder of all repo locks (default: the per-user
    data folder's `merge-lock`)."""

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
    bound = settings.lock_wait_minutes * 60
    displaced: Holder | None = None
    while (tried := _take(folder)) != _TAKEN:
        displaced = None  # a lock this loop does not clear is no dead holder's
        found = holder_of(repo, root=root)
        waited = (clock() - started).total_seconds()
        if found is None:  # released between the try and the read, or the folder cannot be made
            if waited >= bound:
                raise LockError("lock_unwritable" if tried == _DENIED else "lock_busy", str(folder))
            sleep(min(1.0, settings.poll_seconds, bound - waited))
            continue
        if _dead_here(found) and _clear_dead(repo, folder, found, root=root, sleep=sleep):
            displaced = found  # the dead holder's lock is gone: the take is tried again at once
            continue
        if found.age_minutes(clock()) > settings.lock_stale_minutes:
            raise LockError("lock_stale", f"{repo} held by {found.said()}", found)
        # a folder taken a moment ago whose owner file is still being written names its holder soon
        being_written = found.holder == UNKNOWN and found.age_minutes(clock()) < WRITE_GRACE_MINUTES
        if not told and on_wait is not None and not being_written:
            on_wait(found)
            told = True
        if waited >= bound:
            raise LockError("lock_busy", f"{repo} held by {found.said()}", found)
        sleep(min(settings.poll_seconds, bound - waited))
    mine = Holder(holder, clock(), os.getpid(), machine_id(), uuid.uuid4().hex, started=_process_started(),
                  taken_from=displaced)
    try:
        _write_owner(folder, mine)
    except OSError as e:
        _remove(folder, tries=1, sleep=sleep)
        raise LockError("lock_unwritable", f"{folder}: {e}") from None
    try:
        yield mine
    finally:
        _release(folder, mine.token, sleep)


def holder_of(repo: str, *, root: Path | None = None) -> Holder | None:
    """Who holds the repo's lock, None when nobody does."""

    folder = lock_folder(repo, root=root)
    try:
        taken = datetime.fromtimestamp(folder.stat().st_mtime, UTC)
    except FileNotFoundError:
        return None
    try:
        owner = json.loads((folder / OWNER_FILE).read_text("utf-8"))
        since = datetime.fromisoformat(owner["since"])
        return Holder(str(owner["holder"]), since if since.tzinfo else since.replace(tzinfo=UTC),
                      _of_type(owner.get("pid"), int), _of_type(owner.get("machine"), str),
                      _of_type(owner.get("token"), str), _of_type(owner.get("started"), float))
    except (OSError, ValueError, KeyError, TypeError):
        return Holder(UNKNOWN, taken)


def unlock_stale(repo: str, *, settings: MergeSettings, root: Path | None = None,
                 clock: Callable[[], datetime] = _now, sleep: Callable[[float], None] = time.sleep) -> Holder:
    """Remove the repo's lock, only when it is stale; returns who held it. A lock it cannot
    remove whole is `lock_unwritable`, and still names its holder."""

    found = holder_of(repo, root=root)
    if found is None:
        raise LockError("lock_missing", repo)
    if found.age_minutes(clock()) <= settings.lock_stale_minutes:
        raise LockError("lock_not_stale", f"{repo} held by {found.said()}", found)
    folder = lock_folder(repo, root=root)
    if not _remove(folder, tries=RELEASE_TRIES, sleep=sleep):
        raise LockError("lock_unwritable", f"{folder} could not be removed", found)
    return found


def _of_type(value: object, kind: type) -> object:
    return value if isinstance(value, kind) and not isinstance(value, bool) else None


def _process_started() -> float | None:
    """This process's start time, the one the owner file records beside its pid."""

    try:
        return psutil.Process().create_time()
    except psutil.Error:
        return None


def _dead_here(found: Holder) -> bool:
    """True when the lock's holder ran on this machine and is dead: its pid does not exist, or the
    process holding it started at another time than the owner file records (pid reuse). A lock naming
    another machine is never judged by its pid; an owner file with no start time is judged by its pid
    alone; a process that cannot be read is not proven dead."""

    if found.pid is None or found.pid <= 0 or found.machine != machine_id():
        return False
    try:
        process = psutil.Process(found.pid)
        return found.started is not None and abs(process.create_time() - found.started) > START_TOLERANCE
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


def _clear_dead(repo: str, folder: Path, found: Holder, *, root: Path | None,
                sleep: Callable[[float], None]) -> bool:
    """Remove the lock `found` read from a dead holder, if it is still that lock: another line may
    have taken the folder since. True when the folder is gone."""

    if holder_of(repo, root=root) != found:
        return False
    return _remove(folder, tries=RELEASE_TRIES, sleep=sleep)


def _take(folder: Path) -> str:
    try:
        folder.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:  # FileExistsError too: a file where the folder of locks should be
        raise LockError("lock_unwritable", f"{folder.parent}: {e}") from None
    try:
        folder.mkdir()
        return _TAKEN
    except FileExistsError:
        return _HELD
    except PermissionError:
        return _DENIED  # Windows: a folder being removed this moment, or no right to make it
    except OSError as e:
        raise LockError("lock_unwritable", f"{folder}: {e}") from None


def _write_owner(folder: Path, mine: Holder) -> None:
    """The owner file written whole: a reader sees no file or the whole of it."""

    text = json.dumps({"holder": mine.holder, "since": mine.since.isoformat(), "pid": mine.pid,
                       "machine": mine.machine, "token": mine.token, "started": mine.started})
    temporary = folder.parent / f".{folder.name}.{mine.token}.tmp"
    temporary.write_text(text, "utf-8")
    os.replace(temporary, folder / OWNER_FILE)


def _release(folder: Path, token: str | None, sleep: Callable[[float], None]) -> None:
    try:
        owner = json.loads((folder / OWNER_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(owner, dict) or owner.get("token") != token:
        return  # no longer ours: removed as stale and taken by another line
    _remove(folder, tries=RELEASE_TRIES, sleep=sleep)


def _remove(folder: Path, *, tries: int, sleep: Callable[[float], None]) -> bool:
    """Remove the lock: every other file first, the owner file last, then the folder; True when
    it is gone. A failure leaves the owner file, so the lock still says whose it is."""

    for attempt in range(tries):
        try:
            if folder.is_dir():
                for path in folder.iterdir():
                    if path.name != OWNER_FILE:
                        path.unlink()
                (folder / OWNER_FILE).unlink(missing_ok=True)
                folder.rmdir()
            else:
                folder.unlink(missing_ok=True)  # a file standing where the folder would be
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 < tries:
                sleep(0.2)
    return not folder.exists()
