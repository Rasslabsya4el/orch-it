"""The PreToolUse hook end to end (#395 rows 9, 10): a real repo with worktrees, a real store,
and `run_pre_tool_use` reading a hook payload. A delete of a protected worktree is denied and
writes a `guard.fired` deny; a harmless command passes; a delete inside one's own worktree
passes; a delete of another repo's worktree from outside it is still denied (the fail-open gaps
the brain named); a false fire is reversed; `guard-fires` counts what was written."""

import json
import subprocess
import sys

import pytest

from orch_it.events.store import Store
from orch_it.guards import hook

WINDOWS = sys.platform == "win32"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture(autouse=True)
def hook_settings(tmp_path, monkeypatch):
    # not named `isolated_settings`: that name would shadow tests/conftest.py's fixture, which gives the
    # test its non-git cwd (#453 unit 1); both run, this one's ORCH_IT_SETTINGS (set later) wins.
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)
    (tmp_path / "settings.json").write_text('{"repo": "acme-x/widgets-y"}', "utf-8")  # the repo the events are written under


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "repo"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
        "--allow-empty", "-m", "x")
    own = main / ".claude" / "worktrees" / "own"
    other = main / ".claude" / "worktrees" / "other"
    git(main, "worktree", "add", "-q", str(own), "-b", "own")
    git(main, "worktree", "add", "-q", str(other), "-b", "other")
    (own / "scratch").mkdir()
    (other / "src").mkdir()
    return {"main": main, "own": own, "other": other}


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")   # outside any git tree


def fwd(path):
    """A path as a Bash command would carry it: forward slashes, since a backslash is an escape
    in a POSIX command (a Windows agent passes `C:/...` to the Bash tool)."""

    return str(path).replace("\\", "/")


def payload(command, cwd, tool="Bash"):
    return json.dumps({"tool_name": tool, "tool_input": {"command": command},
                       "cwd": str(cwd), "session_id": "s-test"})


def run(command, cwd, store, tool="Bash"):
    text, code = hook.run_pre_tool_use(payload(command, cwd, tool), store)
    return text, code


def denies(text):
    return bool(text) and json.loads(text)["hookSpecificOutput"]["permissionDecision"] == "deny"


def fired(store):
    return store.query("SELECT guard, verdict, rule_id FROM events WHERE kind = 'guard.fired'").rows


def test_a_delete_of_another_worktree_is_denied_and_recorded(repo, store):
    text, code = run(f"rm -rf {fwd(repo['other'])}", repo["own"], store)
    assert denies(text) and code == 0
    rows = fired(store)
    assert any(g == "worktree_delete" and v == "deny" for g, v, _ in rows)


def test_a_harmless_command_passes_and_writes_nothing(repo, store):
    text, _ = run("ls -la", repo["own"], store)
    assert not text
    assert fired(store) == []


def test_a_delete_inside_the_own_worktree_passes(repo, store):
    text, _ = run("rm -rf scratch", repo["own"], store)
    assert not denies(text)
    assert any(v == "pass" for _, v, _ in fired(store))


def test_a_delete_of_a_worktree_from_outside_any_repo_is_still_denied(repo, store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    text, _ = run(f"rm -rf {fwd(repo['other'])}", outside, store)
    assert denies(text), "a delete from outside the repo must still be refused"


def test_a_guard_that_crashes_lets_the_command_through(repo, store, monkeypatch):
    monkeypatch.setattr(hook.World, "protected_roots",
                        lambda self, cwd: (_ for _ in ()).throw(RuntimeError("boom")))
    text, code = run(f"rm -rf {fwd(repo['other'])}", repo["own"], store)
    assert not text and code == 0  # failed open
    errs = store.query("SELECT failed_open FROM events WHERE kind = 'error.raised'").rows
    assert errs and errs[0][0]


def test_a_false_fire_is_reversed_and_counted(repo, store):
    run(f"rm -rf {fwd(repo['other'])}", repo["own"], store)
    deny_id = store.query(
        "SELECT id FROM events WHERE kind = 'guard.fired' AND verdict = 'deny'").rows[0][0]
    hook.reverse(store, deny_id, by="owner")
    reversed_rows = store.query("SELECT fired_event FROM events WHERE kind = 'guard.reversed'").rows
    assert reversed_rows and reversed_rows[0][0] == deny_id
    counts = {r[0]: r for r in hook.guard_fires(store).rows}
    # worktree_delete has at least one deny and one reversal in the counts
    assert any(row[2] >= 1 and row[4] >= 1 for row in hook.guard_fires(store).rows)


def test_reverse_refuses_an_id_that_is_no_deny(store):
    with pytest.raises(hook.ReverseError):
        hook.reverse(store, "nope", by="owner")


@pytest.mark.parametrize("raw", ["", "   ", "not json", "[]", "5", "null",
                                 '{"tool_input": "x"}', '{"cwd": 5, "tool_input": {"command": 1}}'])
def test_the_hook_never_raises_or_blocks_on_bad_input(raw, store):
    text, code = hook.run_pre_tool_use(raw, store)
    assert code == 0 and text == ""


def test_a_deny_is_reversed_only_once(repo, store):
    run(f"rm -rf {fwd(repo['other'])}", repo["own"], store)
    deny_id = store.query("SELECT id FROM events WHERE verdict = 'deny'").rows[0][0]
    hook.reverse(store, deny_id, by="owner")
    with pytest.raises(hook.ReverseError):
        hook.reverse(store, deny_id, by="owner")


def test_a_deny_with_no_repo_known_is_still_made_writes_no_event_and_says_so(repo, store, tmp_path, capsys):
    # no setting `repo` and no origin in the work tree: the decision is made, its events are not written.
    # This is the one case where ADR 0004's "every triggered command writes guard.fired" does not hold.
    (tmp_path / "settings.json").write_text("{}", "utf-8")
    text, code = run(f"rm -rf {fwd(repo['other'])}", repo["own"], store)
    assert denies(text) and code == 0
    assert store.query("SELECT count(*) FROM events").rows == [(0,)]
    assert "no repo is known" in capsys.readouterr().err
