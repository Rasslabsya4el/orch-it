"""The merge flow's commands (#400): `merge unlock` removes only a stale repo lock and says whose it
was, with a `lock.removed` event; a refusal prints `error: <code>` and exits 2."""

from datetime import UTC, datetime, timedelta

import pytest

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.merge import lock
from orch_it.merge.settings import MergeSettings

REPO = "owner-x/repo-y"


@pytest.fixture
def locks(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setattr(lock.installation, "data_folder", lambda: tmp_path / "data")
    return tmp_path / "data" / lock.FOLDER


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_unlock_removes_a_stale_lock_and_writes_whose_it_was(locks, tmp_path, capsys):
    long_ago = datetime.now(UTC) - timedelta(hours=3)
    held = lock.hold(REPO, "merge line owner-x/repo-y", settings=MergeSettings(), clock=lambda: long_ago)
    held.__enter__()
    store = str(tmp_path / "events")
    code, out, err = run(capsys, "--store", store, "merge", "unlock", "--repo", REPO)
    assert (code, err) == (0, "")
    assert out.startswith("removed the stale lock of owner-x/repo-y: merge line owner-x/repo-y since ")
    assert lock.holder_of(REPO) is None
    rows = Store.open(store).query(
        "SELECT json_extract(detail, '$.holder'), json_extract(detail, '$.held_minutes') >= 180"
        " FROM events WHERE kind = 'lock.removed'").rows
    assert rows == [("merge line owner-x/repo-y", 1)]


def test_unlock_refuses_a_lock_that_is_not_stale_and_leaves_it(locks, tmp_path, capsys):
    with lock.hold(REPO, "merge line now", settings=MergeSettings()):
        code, out, err = run(capsys, "--store", str(tmp_path / "events"), "merge", "unlock", "--repo", REPO)
        assert (code, out) == (2, "")
        assert err.startswith("error: lock_not_stale: owner-x/repo-y held by merge line now since ")
        assert lock.holder_of(REPO).holder == "merge line now"


def test_unlock_with_no_lock_says_so(locks, tmp_path, capsys):
    code, _, err = run(capsys, "--store", str(tmp_path / "events"), "merge", "unlock", "--repo", REPO)
    assert code == 2 and err.startswith("error: lock_missing")
