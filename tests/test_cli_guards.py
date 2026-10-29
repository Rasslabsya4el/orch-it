"""The CLI wiring of the guards (#395 unit 5): `hook pre-tool-use` reads the hook JSON on stdin
and prints the deny decision, `guards reverse` marks a false fire, and `events guard-fires`
prints the counts."""

import json
import subprocess
import sys

import pytest

from orch_it.cli import main
from orch_it.events.store import Store


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text('{"repo": "acme-x/widgets-y"}', "utf-8")  # the repo the events are written under
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def worktrees(tmp_path):
    main_repo = tmp_path / "repo"
    main_repo.mkdir()
    git(main_repo, "init", "-q", "-b", "main")
    git(main_repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
        "--allow-empty", "-m", "x")
    own = main_repo / ".claude" / "worktrees" / "own"
    other = main_repo / ".claude" / "worktrees" / "other"
    git(main_repo, "worktree", "add", "-q", str(own), "-b", "own")
    git(main_repo, "worktree", "add", "-q", str(other), "-b", "other")
    return own, other


def run(capsys, store, *argv, stdin=""):
    import io
    sys.stdin = io.StringIO(stdin)
    try:
        code = main(["--store", str(store.root), *argv])
    finally:
        sys.stdin = sys.__stdin__
    out = capsys.readouterr()
    return code, out.out, out.err


def test_hook_pre_tool_use_denies_a_delete_of_another_worktree(capsys, store, worktrees):
    own, other = worktrees
    payload = json.dumps({"tool_name": "Bash", "cwd": str(own), "session_id": "s",
                          "tool_input": {"command": "rm -rf " + str(other).replace("\\", "/")}})
    code, out, _ = run(capsys, store, "hook", "pre-tool-use", stdin=payload)
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_pre_tool_use_allows_a_harmless_command(capsys, store, worktrees):
    own, _ = worktrees
    payload = json.dumps({"tool_name": "Bash", "cwd": str(own), "session_id": "s",
                          "tool_input": {"command": "ls -la"}})
    code, out, _ = run(capsys, store, "hook", "pre-tool-use", stdin=payload)
    assert code == 0 and out.strip() == ""


def test_guards_reverse_and_guard_fires(capsys, store, worktrees):
    own, other = worktrees
    payload = json.dumps({"tool_name": "Bash", "cwd": str(own), "session_id": "s",
                          "tool_input": {"command": "rm -rf " + str(other).replace("\\", "/")}})
    run(capsys, store, "hook", "pre-tool-use", stdin=payload)
    deny_id = store.query("SELECT id FROM events WHERE verdict = 'deny'").rows[0][0]
    code, out, _ = run(capsys, store, "guards", "reverse", deny_id)
    assert code == 0 and "reversed=" in out
    code, out, _ = run(capsys, store, "events", "guard-fires")
    assert code == 0 and "reversals" in out and "worktree_delete" in out


def test_guards_reverse_an_unknown_id_is_a_coded_error(capsys, store):
    code, _, err = run(capsys, store, "guards", "reverse", "nope")
    assert code == 2 and "error: no_such_fire" in err
