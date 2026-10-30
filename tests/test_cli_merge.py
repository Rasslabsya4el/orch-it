"""The merge flow's commands (#400): `merge unlock` removes only a stale repo lock and says whose it
was, with a `lock.removed` event; a refusal prints `error: <code>` and exits 2."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.merge import commands, lock
from orch_it.merge.settings import MergeSettings
from orch_it.testruns.config import DEFAULT_REQUIRED_CHECKS

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


def test_merge_line_prints_each_head_and_how_the_run_ended(github, tmp_path, capsys, monkeypatch):
    from fake_github import PASSED
    from follow_world import ROWS_NO_AFTER

    hub, _ = github
    monkeypatch.setattr(lock.installation, "data_folder", lambda: tmp_path / "data")
    rows = tmp_path / "rows.md"
    rows.write_text(ROWS_NO_AFTER, "utf-8")
    store = str(tmp_path / "events")
    run(capsys, "--store", store, "pr", "open", "10", "--acceptance", str(rows), "--repo", REPO)
    pr = max(hub.pulls)
    hub.checks_on(hub.head(pr), PASSED)
    code, out, err = run(capsys, "--store", store, "merge", "line", "--repo", REPO)
    assert (code, err) == (0, "")
    lines = out.splitlines()  # the work tree here is no clone of the repo: the line says so and goes one by one
    assert lines[0].startswith("batch off: ") and lines[1].startswith(f"#{pr} merged waited ")
    assert lines[-1] == "line of owner-x/repo-y into main: merged=1 left=0 empty"


def test_merge_line_says_who_holds_the_line_and_gives_up(github, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(lock.installation, "data_folder", lambda: tmp_path / "data")
    (tmp_path / "settings.json").write_text('{"merge": {"lock_wait_minutes": 0.02, "poll_seconds": 1}}', "utf-8")
    with lock.hold(REPO, "merge line of another session", settings=MergeSettings()):
        code, out, err = run(capsys, "--store", str(tmp_path / "events"), "merge", "line", "--repo", REPO)
    lines = out.splitlines()  # `batch off:` comes first (no clone here), then the wait
    assert code == 2 and lines[0].startswith("batch off: ")
    assert lines[1].startswith("waits: the line of owner-x/repo-y is held by merge line of another session")
    assert err.startswith("error: lock_busy")


def test_main_check_prints_what_it_did_with_red_main(github, tmp_path, capsys):
    from fake_github import FAILED, PASSED

    hub, _ = github
    hub.main_run(hub.land(number=57), dict(zip(DEFAULT_REQUIRED_CHECKS, (FAILED, PASSED))), failing=["t::a"])
    code, out, err = run(capsys, "--store", str(tmp_path / "events"), "main", "check", "--repo", REPO)
    number = max(n for (r, n) in hub.issues if not hub.issues[(r, n)].is_pr)
    assert (code, err) == (0, "") and out.startswith(f"main red at m") and out.rstrip().endswith(f"opened=#{number}")


def test_reconcile_says_whether_actions_are_on_and_what_it_repaired(github, tmp_path, capsys, monkeypatch):
    from fake_github import PASSED
    from follow_world import ROWS_NO_AFTER

    hub, _ = github
    rows = tmp_path / "rows.md"
    rows.write_text(ROWS_NO_AFTER, "utf-8")
    store = str(tmp_path / "events")
    run(capsys, "--store", store, "pr", "open", "10", "--acceptance", str(rows), "--repo", REPO)
    pr = max(hub.pulls)
    hub.checks_on(hub.head(pr), PASSED)
    hub.merge(REPO, pr, hub.head(pr), "merge")
    hub.actions = False
    monkeypatch.setenv("GITHUB_ACTIONS", "true")  # no events of workflows to bring in from inside one
    code, out, err = run(capsys, "--store", store, "reconcile", "--repo", REPO)
    assert (code, err) == (0, "")
    assert out.splitlines()[0] == "actions=off: the sweep runs here"
    assert f"closed #{pr}" in out and not hub.issues[(REPO, 10)].open


def test_pr_open_with_an_acceptance_file_that_is_not_there_is_refused(github, tmp_path, capsys):
    code, out, err = run(capsys, "--store", str(tmp_path / "events"), "pr", "open", "10", "--acceptance",
                         str(tmp_path / "none.md"), "--repo", REPO)
    assert (code, out) == (2, "") and err.startswith("error: acceptance_unreadable")


@pytest.fixture
def seen(github, tmp_path, monkeypatch):
    """`run_line` replaced: it answers an empty run and notes the `suite` it was given."""
    from orch_it.merge.line import LineReport

    monkeypatch.setattr(lock.installation, "data_folder", lambda: tmp_path / "data")
    given = []

    def fake(hub, store, repo, base, **kwargs):
        given.append(kwargs.get("suite"))
        return LineReport(repo, base, (), "empty")

    monkeypatch.setattr(commands, "run_line", fake)
    return given


def clone_of_repo(folder):
    from orch_it.merge import git as merge_git

    folder.mkdir()
    owner, name = REPO.split("/")
    for args in (["init", "--quiet"], ["remote", "add", "origin", f"https://github.com/{owner}/{name}.git"]):
        assert merge_git.run(["-C", str(folder), *args]).code == 0
    return folder


def test_merge_line_hands_run_line_a_suite_when_batch_size_is_two_or_more_and_none_when_it_is_one(
        seen, tmp_path, capsys):
    tree = clone_of_repo(tmp_path / "clone")
    store, settings = str(tmp_path / "events"), tmp_path / "settings.json"
    for size, expect_suite in ((4, True), (2, True), (1, False)):
        settings.write_text(f'{{"merge": {{"batch_size": {size}}}}}', "utf-8")
        seen.clear()
        code, out, err = run(capsys, "--store", store, "merge", "line", "--repo", REPO, "--work-tree", str(tree))
        assert (code, err) == (0, "")
        assert out == f"line of {REPO} into main: merged=0 left=0 empty" + chr(10)
        assert len(seen) == 1 and callable(seen[0]) == expect_suite and (seen[0] is None) == (not expect_suite)


def test_merge_line_hands_run_line_a_priority_hold_only_when_a_repo_of_its_account_is_set_above_it(
        github, tmp_path, capsys, monkeypatch):
    from orch_it.merge.line import LineReport

    monkeypatch.setattr(lock.installation, "data_folder", lambda: tmp_path / "data")
    given = []

    def fake(hub, store, repo, base, **kwargs):
        given.append(kwargs.get("priority_hold"))
        return LineReport(repo, base, (), "empty")

    monkeypatch.setattr(commands, "run_line", fake)
    tree = clone_of_repo(tmp_path / "clone")
    settings_file = tmp_path / "settings.json"
    owner = REPO.split("/")[0]
    for text, expect_hold in (("{}", False),
                              (json.dumps({"repos": {REPO: {"github_priority": 1}}}), False),
                              (json.dumps({"repos": {f"{owner}/other-z": {"github_priority": 4}}}), True),
                              (json.dumps({"repos": {"stranger-q/other-z": {"github_priority": 4}}}), False)):
        settings_file.write_text(text, "utf-8")
        given.clear()
        code, _, err = run(capsys, "--store", str(tmp_path / "events"), "merge", "line", "--repo", REPO,
                           "--work-tree", str(tree))
        assert (code, err) == (0, "") and len(given) == 1 and callable(given[0]) == expect_hold, text


def test_merge_line_prints_batch_off_before_the_line_runs_and_its_notes_before_the_summary(
        github, tmp_path, capsys, monkeypatch):
    from orch_it.merge.line import LineReport, Served

    monkeypatch.setattr(lock.installation, "data_folder", lambda: tmp_path / "data")
    printed = []

    def fake(hub, store, repo, base, **kwargs):
        printed.append(capsys.readouterr().out)  # what the command had said when the line began
        return LineReport(repo, base, (Served(7, None, "merged", 1.0, 0),), "empty",
                          ("batch landing refused: Changes must be made through a pull request.",))

    monkeypatch.setattr(commands, "run_line", fake)
    (tmp_path / "plain").mkdir()
    code, out, err = run(capsys, "--store", str(tmp_path / "events"), "merge", "line", "--repo", REPO,
                         "--work-tree", str(tmp_path / "plain"))
    assert (code, err) == (0, "") and len(printed) == 1 and printed[0].startswith("batch off: no work tree of ")
    assert out.splitlines() == ["#7 merged waited 1 min updates 0",
                                "batch landing refused: Changes must be made through a pull request.",
                                f"line of {REPO} into main: merged=1 left=0 empty"]


def test_merge_line_with_no_clone_of_the_repo_goes_one_by_one_and_says_so(seen, tmp_path, capsys):
    (tmp_path / "plain").mkdir()
    code, out, err = run(capsys, "--store", str(tmp_path / "events"), "merge", "line", "--repo", REPO,
                         "--work-tree", str(tmp_path / "plain"))
    assert (code, err) == (0, "") and seen == [None]
    first, last = out.splitlines()
    assert first.startswith("batch off: no work tree of owner-x/repo-y at ") and "one pull request at a time" in first
    assert last == f"line of {REPO} into main: merged=0 left=0 empty"
