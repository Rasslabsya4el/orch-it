"""From one tool call's command text to the delete, move and stop actions it would perform
(#395 decisions 1, 2, 3): the trigger. It reads the words that can delete, move or stop and the
object each would act on, as spelled; the verdict is read later from the object, never here. No
way of writing such a command may slip past; a look-alike yields nothing.

`actions(command, tool=, cwd=, env=, windows=)` is the seam. `tool` is "Bash" (a POSIX shell;
Git Bash on Windows) or "PowerShell"; wrappers switch the dialect."""

from orch_it.guards.triggers import Delete, Move, Stop, actions


def act(command, tool="Bash", cwd="/work", env=None, windows=False):
    return actions(command, tool=tool, cwd=cwd, env=env or {}, windows=windows)


def deletes(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Delete)]


def test_a_plain_rm_is_a_delete_of_its_target(self_check=None):
    found = deletes("rm -rf /work/wt")
    assert len(found) == 1
    assert found[0].targets == ("/work/wt",)
    assert found[0].recursive is True
    assert found[0].rule_id == "rm"
    assert found[0].cwd == "/work"


def test_a_command_with_no_trigger_yields_nothing():
    assert act("ls -la /work/wt") == []
    assert act("git worktree list") == []
    assert act("cat /work/wt/notes.txt") == []


import pytest


# --- row 1: every way of writing a delete or move is a trigger -------------------------------

# (command, tool, windows, expected rule_id) — each must yield a Delete or Move whose first
# readable target is /work/wt (or a None target when the way hides it); the verdict is the
# guard's, read later from the path.
DELETE_WAYS = [
    ("rm -rf /work/wt", "Bash", False, "rm"),
    ("rm -r /work/wt", "Bash", False, "rm"),
    ("rm -fr /work/wt", "Bash", False, "rm"),
    ("rm --recursive --force /work/wt", "Bash", False, "rm"),
    ("rm /work/wt/file", "Bash", False, "rm"),
    ("unlink /work/wt", "Bash", False, "unlink"),
    ("rmdir /work/wt", "Bash", False, "rmdir"),
    ("/bin/rm -rf /work/wt", "Bash", False, "rm"),
    ("command rm -rf /work/wt", "Bash", False, "rm"),
    ("sudo rm -rf /work/wt", "Bash", False, "rm"),
    ("env FOO=1 rm -rf /work/wt", "Bash", False, "rm"),
    ("nohup rm -rf /work/wt", "Bash", False, "rm"),
    ("nice -n 5 rm -rf /work/wt", "Bash", False, "rm"),
    ("timeout 5 rm -rf /work/wt", "Bash", False, "rm"),
    ("timeout --signal=9 5s rm -rf /work/wt", "Bash", False, "rm"),
    ("bash -c 'rm -rf /work/wt'", "Bash", False, "rm"),
    ("sh -c \"rm -rf /work/wt\"", "Bash", False, "rm"),
    ("find /work/wt -delete", "Bash", False, "find-delete"),
    ("find /work/wt -exec rm -rf {} +", "Bash", False, "find-exec-rm"),
    ("rsync -a --delete /empty/ /work/wt/", "Bash", False, "rsync-delete"),
    ("git worktree remove /work/wt", "Bash", False, "git-worktree-remove"),
    ("git worktree remove --force /work/wt", "Bash", False, "git-worktree-remove"),
    ("git worktree prune", "Bash", False, "git-worktree-prune"),
    ("python -c \"import shutil; shutil.rmtree('/work/wt')\"", "Bash", False, "python-c"),
    ("python3 -c 'import os; os.remove(\"/work/wt\")'", "Bash", False, "python-c"),
    ("node -e \"require('fs').rmSync('/work/wt',{recursive:true})\"", "Bash", False, "node-e"),
    ("mv /work/wt /tmp/elsewhere", "Bash", False, "mv"),
    ("cd /work && rm -rf wt", "Bash", False, "rm"),
    ("Remove-Item -Recurse -Force C:/work/wt", "PowerShell", True, "remove-item"),
    ("ri -r C:/work/wt", "PowerShell", True, "remove-item"),
    (r"rd /s /q C:\work\wt", "PowerShell", True, "rmdir"),
    (r"del /s /q C:\work\wt", "PowerShell", True, "del"),
    ("Move-Item C:/work/wt C:/tmp/x", "PowerShell", True, "move-item"),
    (r"cmd /c rmdir /s /q C:\work\wt", "PowerShell", True, "rmdir"),
    (r"cmd /c rd /s C:\work\wt", "Bash", True, "rmdir"),
    ("powershell -Command \"Remove-Item -Recurse C:/work/wt\"", "Bash", True, "remove-item"),
    ("pwsh -c 'Remove-Item -Recurse C:/work/wt'", "PowerShell", True, "remove-item"),
    (r"robocopy C:\empty C:\work\wt /MIR", "PowerShell", True, "robocopy-mir"),
    ("git worktree move /work/wt /work/moved", "Bash", False, "git-worktree-move"),
    ("rm -rf -- /work/wt", "Bash", False, "rm"),
    ("Remove-Item C:/work/wt -Recurse", "PowerShell", True, "remove-item"),
]


@pytest.mark.parametrize("command,tool,windows,rule", DELETE_WAYS)
def test_every_delete_or_move_way_is_a_trigger(command, tool, windows, rule):
    found = [a for a in act(command, tool=tool, windows=windows)
             if isinstance(a, (Delete, Move)) and a.rule_id == rule]
    assert found, f"no {rule} action from {command!r}: got {act(command, tool=tool, windows=windows)}"


def targets_of(action):
    return action.sources if isinstance(action, Move) else action.targets


@pytest.mark.parametrize("command,tool,windows,rule", DELETE_WAYS)
def test_a_delete_way_names_its_object_or_marks_it_unreadable(command, tool, windows, rule):
    found = [a for a in act(command, tool=tool, windows=windows)
             if isinstance(a, (Delete, Move)) and a.rule_id == rule][0]
    targets = targets_of(found)
    # every readable target is a real string (the guard resolves it); none is empty
    assert targets, command
    assert all(t is None or isinstance(t, str) and t for t in targets), command


def test_the_past_gaps_are_closed():
    # cmd /c rmdir /s unwrapped (P465)
    assert any(isinstance(a, Delete) for a in act(r"cmd /c rmdir /s C:\work\wt", tool="PowerShell", windows=True))
    # a refused delete done again through a python call (P835)
    assert any(isinstance(a, Delete) for a in act("python -c \"import shutil;shutil.rmtree('/work/wt')\"", windows=False))
    # git worktree remove --force on a worktree holding a junction (the incident): the trigger fires
    assert any(a.rule_id == "git-worktree-remove" for a in act("git worktree remove --force /work/wt")
               if isinstance(a, Delete))


# --- row 1: look-alikes yield nothing --------------------------------------------------------

LOOK_ALIKES = [
    "rm -rf /work/scratch",            # the agent's own scratch folder (a Delete, but target decides; here just that it is read)
    "ls -la /work/wt",
    "git worktree list",
    "git status",
    "cat /work/wt/notes.txt",
    "grep -r delete /work/wt",
    "echo rm -rf /work/wt",
    "echo 'rm -rf /work/wt'",
    "printf 'delete everything'",
    "python -c \"x=[1,2,3]; x.remove(2); print(x)\"",
    "python -c \"print('rm -rf /x is just text')\"",
    "lua -e 'table.remove(t, 1)'",
    "node -e \"[1,2].splice(0,1)\"",
    r"mklink /J C:\link C:\target",
    "ln -s /work/wt /tmp/link",
    "find /work/wt -name '*.log'",
    "find /work/wt -type f -print",
    "rsync -a /src/ /work/wt/",
    r"robocopy C:\a C:\b /E",
    "wait",
    "Wait-Process -Name python",
    "git worktree add /work/new",
    "cp -r /work/wt /backup",
    "touch /work/wt/x",
]


@pytest.mark.parametrize("command", LOOK_ALIKES)
def test_a_look_alike_that_deletes_nothing_yields_no_delete(command):
    windows = "\\" in command or command.startswith(("Wait-Process", "mklink", "robocopy"))
    tool = "PowerShell" if command.startswith(("Wait-Process", "Remove")) else "Bash"
    out = act(command, tool=tool, windows=windows)
    if command == "rm -rf /work/scratch":
        # this one IS a delete; the guard passes it because /work/scratch is no protected root
        assert any(isinstance(a, Delete) for a in out)
    else:
        assert not any(isinstance(a, (Delete, Move)) for a in out), (command, out)


def test_a_delete_word_in_a_heredoc_body_is_not_a_command():
    command = "cat <<'EOF' > /work/wt/readme\nrm -rf /work/wt\nEOF"
    out = act(command)
    assert not any(isinstance(a, Delete) for a in out)


def test_a_delete_through_an_unreadable_target_is_a_delete_with_a_none_target():
    out = act("rm -rf $UNKNOWN", windows=False)
    deletes = [a for a in out if isinstance(a, Delete)]
    assert deletes and None in deletes[0].targets
    out = act('rm -rf "$(cat list.txt)"', windows=False)
    assert any(None in a.targets for a in out if isinstance(a, Delete))
