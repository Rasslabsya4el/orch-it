"""The repo lock of the merge line (#400 decision 2, row 7): one holder at a time per repo, taken by
one atomic folder creation, released on exit whatever happens; a second line waits, says who
holds it, and gives up at its bound; a lock older than its stale bound is reported and never taken,
and only the explicit `unlock_stale` removes it."""

import json
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orch_it.merge import lock
from orch_it.merge.lock import LockError
from orch_it.merge.settings import MergeSettings

REPO = "owner-x/repo-y"
START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


class Clock:
    """Time that moves only when the lock sleeps; `during_sleep` runs once per sleep."""

    def __init__(self, during_sleep=None):
        self.now = START
        self.slept = []
        self.during_sleep = during_sleep

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)
        if self.during_sleep:
            self.during_sleep(self)


def settings(**values):
    return MergeSettings(**values)


def hold(root, clock, holder="line A", told=None, **values):
    return lock.hold(REPO, holder, settings=settings(**values), root=root, clock=clock, sleep=clock.sleep,
                     on_wait=(told.append if told is not None else None))


def test_a_free_lock_is_taken_and_released_on_exit(tmp_path):
    clock = Clock()
    with hold(tmp_path, clock) as held:
        assert held.holder == "line A" and held.since == START
        assert lock.holder_of(REPO, root=tmp_path).holder == "line A"
    assert lock.holder_of(REPO, root=tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_it_is_released_when_the_body_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with hold(tmp_path, Clock()):
            raise RuntimeError("the line broke")
    assert lock.holder_of(REPO, root=tmp_path) is None


def test_a_second_line_waits_says_who_holds_it_and_gives_up_at_its_bound(tmp_path):
    clock, told = Clock(), []
    with hold(tmp_path, clock, holder="line A"):
        with pytest.raises(LockError) as caught:
            with hold(tmp_path, clock, holder="line B", told=told, lock_wait_minutes=2, poll_seconds=30):
                pytest.fail("two holders")
    assert caught.value.code == "lock_busy"
    assert caught.value.holder.holder == "line A" and "line A" in str(caught.value)
    assert len(told) == 1 and told[0].holder == "line A"  # said once, not once per poll
    assert sum(clock.slept) >= 120 and max(clock.slept) <= 30


def test_a_waiting_line_takes_the_lock_once_its_holder_releases_it(tmp_path):
    first = hold(tmp_path, Clock(), holder="line A")
    first.__enter__()

    def release_after_a_minute(clock):
        if clock.now - START >= timedelta(minutes=1):
            first.__exit__(None, None, None)
            clock.during_sleep = None

    clock = Clock(release_after_a_minute)
    with hold(tmp_path, clock, holder="line B", lock_wait_minutes=5, poll_seconds=20) as held:
        assert held.holder == "line B"
        assert lock.holder_of(REPO, root=tmp_path).holder == "line B"


def test_a_stale_lock_is_reported_and_never_taken(tmp_path):
    clock = Clock()
    dead = hold(tmp_path, clock, holder="line A")  # a line that died holding it
    dead.__enter__()
    clock.now += timedelta(minutes=121)
    with pytest.raises(LockError) as caught:
        with hold(tmp_path, clock, holder="line B"):
            pytest.fail("a stale lock was taken")
    assert caught.value.code == "lock_stale" and caught.value.holder.holder == "line A"
    assert clock.slept == []  # reported at once, no wait on a lock nobody will release
    assert lock.holder_of(REPO, root=tmp_path).holder == "line A"


def test_unlock_stale_removes_only_a_stale_lock(tmp_path):
    clock = Clock()
    dead = hold(tmp_path, clock, holder="line A")  # kept: a collected holder would release
    dead.__enter__()
    with pytest.raises(LockError) as caught:
        lock.unlock_stale(REPO, settings=settings(), root=tmp_path, clock=clock)
    assert caught.value.code == "lock_not_stale"
    clock.now += timedelta(minutes=121)
    removed = lock.unlock_stale(REPO, settings=settings(), root=tmp_path, clock=clock)
    assert removed.holder == "line A" and lock.holder_of(REPO, root=tmp_path) is None
    with pytest.raises(LockError) as caught:
        lock.unlock_stale(REPO, settings=settings(), root=tmp_path, clock=clock)
    assert caught.value.code == "lock_missing"


def test_a_release_never_removes_a_lock_that_is_no_longer_its_own(tmp_path):
    clock = Clock()
    mine = hold(tmp_path, clock, holder="line A")
    mine.__enter__()
    clock.now += timedelta(minutes=121)
    lock.unlock_stale(REPO, settings=settings(), root=tmp_path, clock=clock)
    with hold(tmp_path, clock, holder="line B"):
        mine.__exit__(None, None, None)  # the old holder ends late
        assert lock.holder_of(REPO, root=tmp_path).holder == "line B"


def test_a_lock_being_written_reads_as_held_and_its_age_comes_from_the_folder(tmp_path):
    clock = Clock()
    clock.now = datetime.now(UTC)  # the folder's own time stands for the moment it was taken
    folder = lock.lock_folder(REPO, root=tmp_path)
    folder.mkdir(parents=True)  # taken a moment ago, its owner file not written yet
    with pytest.raises(LockError) as caught:
        with hold(tmp_path, clock, lock_wait_minutes=1):
            pass
    assert caught.value.code == "lock_busy" and caught.value.holder.holder == "unknown"
    (folder / "owner").write_text("{half", "utf-8")
    found = lock.holder_of(REPO, root=tmp_path)
    assert found.holder == "unknown" and abs(found.since - clock.now) < timedelta(minutes=5)
    clock.now += timedelta(minutes=121)
    with pytest.raises(LockError) as caught:
        with hold(tmp_path, clock):
            pass
    assert caught.value.code == "lock_stale"  # a holder that died while it wrote is stale in time too


@pytest.mark.parametrize("repo", ["owner-x/repo-y", "OWNER-X/Repo-Y"])
def test_one_repo_is_one_lock_whatever_its_letter_case(tmp_path, repo):
    with hold(tmp_path, Clock()):
        with pytest.raises(LockError):
            other = Clock()
            with lock.hold(repo, "line B", settings=settings(lock_wait_minutes=0.5), root=tmp_path,
                           clock=other, sleep=other.sleep):
                pass


def test_two_repos_have_two_locks(tmp_path):
    with hold(tmp_path, Clock()):
        with lock.hold("owner-x/other", "line B", settings=settings(), root=tmp_path, clock=Clock(),
                       sleep=pytest.fail) as held:
            assert held.holder == "line B"


@pytest.mark.parametrize("repo", ["", "no-slash", "../x/y", "a/b/c", "a/..", "a/b\x00"])
def test_a_repo_that_is_no_owner_name_is_refused(tmp_path, repo):
    with pytest.raises(LockError) as caught:
        lock.lock_folder(repo, root=tmp_path)
    assert caught.value.code == "bad_repo"


def test_two_processes_started_at_once_one_holds_the_other_waits_and_names_it(tmp_path):
    # both start on one "go" file; the holder holds until the other has ended (or 60 s)
    script = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from orch_it.merge import lock
        from orch_it.merge.settings import MergeSettings
        name, folder = sys.argv[1], Path(sys.argv[2])
        while not (folder / "go").exists():
            time.sleep(0.01)
        try:
            with lock.hold({REPO!r}, name, settings=MergeSettings(lock_wait_minutes=0.05, poll_seconds=0.5),
                           root=folder / "locks", on_wait=lambda h: print("waits for", h.holder, flush=True)):
                print("holds", flush=True)
                end = time.monotonic() + 60
                while not (folder / "ended").exists() and time.monotonic() < end:
                    time.sleep(0.05)
        except lock.LockError as e:
            print(e.code, flush=True)
            (folder / "ended").write_text("", "utf-8")
    """)
    path = tmp_path / "line.py"
    path.write_text(script, "utf-8")
    lines = [subprocess.Popen([sys.executable, str(path), name, str(tmp_path)], stdout=subprocess.PIPE,
                              text=True, creationflags=NO_WINDOW) for name in ("line A", "line B")]
    (tmp_path / "go").write_text("", "utf-8")
    said = sorted(line.communicate(timeout=90)[0].strip() for line in lines)
    assert said[0] == "holds"
    holder = "line A" if said[1].startswith("waits for line A") else "line B"
    assert said[1].splitlines() == [f"waits for {holder}", "lock_busy"]
    assert not any((tmp_path / "locks").iterdir())


def test_a_waiter_that_reads_the_lock_before_its_owner_file_names_the_holder_once_it_is_written(tmp_path):
    folder = lock.lock_folder(REPO, root=tmp_path)
    folder.mkdir(parents=True)  # the holder made the folder and has not written its owner file yet

    def owner_written(clock):
        (folder / "owner").write_text(json.dumps({"holder": "line A", "since": clock.now.isoformat()}), "utf-8")
        clock.during_sleep = None

    clock, told = Clock(owner_written), []
    clock.now = datetime.now(UTC)
    with pytest.raises(LockError):
        with hold(tmp_path, clock, told=told, lock_wait_minutes=0.5):
            pass
    assert [h.holder for h in told] == ["line A"]


def test_a_since_in_the_future_reads_stale_and_can_be_unlocked(tmp_path):
    clock = Clock()
    folder = lock.lock_folder(REPO, root=tmp_path)
    folder.mkdir(parents=True)
    (folder / "owner").write_text(json.dumps({"holder": "line A\nfrom a skewed clock", "since": "2999-01-01T00:00:00+00:00",
                                              "pid": True, "token": "t"}), "utf-8")
    with pytest.raises(LockError) as caught:
        with hold(tmp_path, clock):
            pass
    assert caught.value.code == "lock_stale"
    assert caught.value.holder.pid is None and "\n" not in caught.value.holder.said()
    assert lock.unlock_stale(REPO, settings=settings(), root=tmp_path, clock=clock).token == "t"
    assert lock.holder_of(REPO, root=tmp_path) is None


def test_a_lock_folder_that_cannot_be_made_is_refused_within_the_wait_bound(tmp_path, monkeypatch):
    clock = Clock()
    real = Path.mkdir

    def denied(self, *args, **kwargs):
        if self.name == "owner-x__repo-y":
            raise PermissionError(13, "Access is denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(LockError) as caught:
        with hold(tmp_path, clock, lock_wait_minutes=1):
            pass
    assert caught.value.code == "lock_unwritable"
    assert 60 <= sum(clock.slept) <= 61  # no spin: it slept its bound away in short steps


def test_a_lock_root_that_is_a_file_is_refused_at_once(tmp_path):
    (tmp_path / "locks").write_text("", "utf-8")
    with pytest.raises(LockError) as caught:
        with hold(tmp_path / "locks", Clock()):
            pass
    assert caught.value.code == "lock_unwritable"


def test_an_unlock_that_cannot_remove_the_lock_says_so_and_the_lock_still_names_its_holder(tmp_path, monkeypatch):
    clock = Clock()
    dead = hold(tmp_path, clock, holder="line A")
    dead.__enter__()
    (lock.lock_folder(REPO, root=tmp_path) / "held-open").write_text("", "utf-8")
    real = Path.unlink

    def held_open(self, *args, **kwargs):
        if self.name == "held-open":
            raise PermissionError(13, "The process cannot access the file")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", held_open)
    clock.now += timedelta(minutes=121)
    with pytest.raises(LockError) as caught:
        lock.unlock_stale(REPO, settings=settings(), root=tmp_path, clock=clock, sleep=clock.sleep)
    assert caught.value.code == "lock_unwritable"
    assert lock.holder_of(REPO, root=tmp_path).holder == "line A"


@pytest.mark.parametrize("repo", ["owner/name.", "owner/.", "-owner/name"])
def test_a_repo_name_windows_would_read_as_another_is_refused(tmp_path, repo):
    with pytest.raises(LockError) as caught:
        lock.lock_folder(repo, root=tmp_path)
    assert caught.value.code == "bad_repo"
