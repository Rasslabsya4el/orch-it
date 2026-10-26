"""The merge flow's commands (#400): `merge unlock` removes only a stale repo lock and says whose it
was, with a `lock.removed` event; a refusal prints `error: <code>` and exits 2."""

from datetime import UTC, datetime, timedelta

import pytest

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.merge import commands, lock
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


@pytest.fixture
def github(tmp_path, monkeypatch):
    from fake_github import FakeGitHub
    from follow_world import NO_AFTER_MERGE, NOW, TITLE

    hub = FakeGitHub(NOW)
    hub.add(10, NO_AFTER_MERGE, ["ready-for-agent"], title=TITLE)
    hub.branches["ticket-10"] = [*hub.branches["main"], "t1"]
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setattr(commands, "GhPullHub", lambda: hub)
    calls = []

    def git(args):
        calls.append(args)
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "ticket-10\n"
        return f"{hub.branches[args[-1].removesuffix('^{commit}')][-1]}\n" if args[:1] == ["rev-parse"] else ""

    monkeypatch.setattr(commands.merge_git, "work_tree", lambda work_tree: git)
    return hub, calls


def test_pr_open_prints_the_pull_request_and_the_park(github, tmp_path, capsys):
    from follow_world import ROWS_NO_AFTER

    hub, calls = github
    rows = tmp_path / "rows.md"
    rows.write_text(ROWS_NO_AFTER, "utf-8")
    code, out, err = run(capsys, "--store", str(tmp_path / "events"), "pr", "open", "10", "--acceptance", str(rows),
                         "--repo", REPO)
    assert (code, err) == (0, "")
    pr = max(hub.pulls)
    until = (datetime.now(UTC) + timedelta(hours=24)).date() + timedelta(days=1)  # the command reads the clock
    assert out == f"pr #{pr} opened at t1: Parked: pr_ended #{pr} until {until.isoformat()}\n"
    assert ["rev-parse", "--abbrev-ref", "HEAD"] in calls


def test_follow_prints_what_it_did_and_an_event_with_no_pull_request_is_skipped(github, tmp_path, capsys):
    import json

    from fake_github import FAILED
    from follow_world import ROWS_NO_AFTER

    hub, _ = github
    rows = tmp_path / "rows.md"
    rows.write_text(ROWS_NO_AFTER, "utf-8")
    store = str(tmp_path / "events")
    run(capsys, "--store", store, "pr", "open", "10", "--acceptance", str(rows), "--repo", REPO)
    pr = max(hub.pulls)
    hub.checks_on(hub.head(pr), FAILED, failing=["t::a"])
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": pr}}), "utf-8")
    assert run(capsys, "--store", store, "follow", "--event", str(event), "--repo", REPO) == (
        0, f"#{pr} red ticket=#10 1 tests\n", "")
    event.write_text(json.dumps({"issue": {"number": 999}}), "utf-8")
    code, out, _ = run(capsys, "--store", store, "follow", "--event", str(event), "--repo", REPO)
    assert (code, out) == (0, "skipped: #999 waits on no pull request of owner-x/repo-y\n")
    code, _, err = run(capsys, "--store", store, "follow", str(pr), "--event", str(event), "--repo", REPO)
    assert code == 2 and err.startswith("error: bad_options")


def test_pr_open_with_an_acceptance_file_that_is_not_there_is_refused(github, tmp_path, capsys):
    code, out, err = run(capsys, "--store", str(tmp_path / "events"), "pr", "open", "10", "--acceptance",
                         str(tmp_path / "none.md"), "--repo", REPO)
    assert (code, out) == (2, "") and err.startswith("error: acceptance_unreadable")
