"""Regressions the unit-2b false-positive review found (#395): these common commands must yield
NO delete/move/stop action (they would block ordinary work once the hook is live), while the
genuine obfuscations still yield an action over their protected target."""

import pytest

from orch_it.guards.triggers import Delete, Move, Stop, actions


def act(command, tool="Bash", cwd="/work/self", windows=False):
    return actions(command, tool=tool, cwd=cwd, env={}, windows=windows)


def deletes(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Delete)]


def guarded(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, (Delete, Move, Stop))]


# a quoted heredoc inside a $() is data (the `git commit -m "$(...)"` / `gh pr create` pattern)
HEREDOC = "cat <<'EOF'\nrm -rf x\nkill 9\nEOF\n"
NO_FIRE = [
    (f'git commit -m "$({HEREDOC})"', "Bash", False),
    (f'gh pr create --body "$({HEREDOC})"', "Bash", False),
    ("for f in *.py; do $PYTHON $f; done", "Bash", False),
    ("$EDITOR", "Bash", False),
    ('bash -c "$CMD"', "Bash", False),
    ("$i++", "PowerShell", True),
    ("$script:count++", "PowerShell", True),
    ("$list.Remove(3)", "PowerShell", True),
    ("($x).Name", "PowerShell", True),
    ("@($a) | Out-Null", "PowerShell", True),
    ("$a, $b = 1, 2", "PowerShell", True),
]


@pytest.mark.parametrize("command,tool,windows", NO_FIRE)
def test_a_common_command_does_not_fire(command, tool, windows):
    assert guarded(command, tool=tool, windows=windows) == [], command


def test_an_unreadable_head_over_another_worktree_still_fires():
    found = [a for a in act("$(echo rm) -rf ../tickets/src") if isinstance(a, Delete)]
    assert found and found[0].targets == ("../tickets/src",) and not found[0].roots_too


def test_an_unreadable_head_over_own_worktree_yields_a_delete_the_guard_will_pass():
    # a tool run with an unreadable head and an own-worktree argument yields a Delete, but with
    # roots_too off and over an own-worktree target, so the guard passes it (not a refusal)
    for command in ("$VENV/bin/python -m pytest .", "$(npm bin)/eslint src/"):
        found = [a for a in act(command) if isinstance(a, Delete)]
        assert found and all(not a.roots_too for a in found), command
        # every target is within the own worktree (cwd), so judge returns pass
        assert all(t is not None and not t.startswith(("/", "..")) for a in found for t in a.targets), command


def test_nesting_past_the_depth_bound_is_refused_as_opaque():
    import orch_it.guards.shell as shell
    deep = "echo " + "$(echo " * (shell.MAX_DEPTH + 5) + "rm -rf x" + ")" * (shell.MAX_DEPTH + 5)
    found = [a for a in act(deep) if isinstance(a, Delete) and a.rule_id == "unreadable-command"]
    assert found and found[0].targets == (None,)  # opaque, the guard refuses it


def test_a_computed_path_delete_in_a_code_body_is_not_refused():
    # the merge-turn release line and the like: os.remove/os.rmdir of a computed path pass
    rel = ("python -c \"import os; p=os.path.join(os.environ['X'],'merge-turn'); "
           "os.remove(os.path.join(p,'owner')); os.rmdir(p)\"")
    assert guarded(rel) == []


def test_a_raw_string_literal_path_in_a_code_body_is_read():
    found = [a for a in act("python -c \"import os; os.remove(r'../tickets/x')\"") if isinstance(a, Delete)]
    assert found and found[0].targets == ("../tickets/x",)


def test_busybox_and_ifs_spliced_deletes_are_read():
    assert [a for a in act("busybox rm -rf ../tickets") if isinstance(a, Delete)][0].targets == ("../tickets",)
    assert [a for a in act("rm${IFS}-rf${IFS}../tickets") if isinstance(a, Delete)][0].targets == ("../tickets",)


def test_a_cd_to_a_windows_drive_path_in_git_bash_replaces_the_cwd():
    # Git Bash on Windows: `cd 'C:/x' && rm -rf junk` must track the drive path as the new cwd,
    # not glue it onto the old one (which lost the dir and refused the relative delete)
    for drive in ("C:/work/other", r"C:\work\other"):
        d = deletes(f"cd '{drive}' && rm -rf junk", windows=True, cwd="C:/work/self")[0]
        assert d.cwd is not None and d.cwd.replace("\\", "/").lower() == "c:/work/other", drive
        assert d.targets == ("junk",)
