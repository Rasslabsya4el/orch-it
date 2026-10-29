"""Hardening of the trigger (#395 unit 2) from the review: it never raises, never lets a
recognised delete or stop through with nothing readable, reads control-flow and eval wrappers,
and does not fire on a look-alike (a delete word inside a code string, a PowerShell flag, a
redirection, a liveness probe)."""

import pytest

from orch_it.guards.triggers import Delete, Move, Stop, actions


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def act(command, tool="Bash", cwd="/work", env=None, windows=False):
    return actions(command, tool=tool, cwd=cwd, env=env or {}, windows=windows)


def deletes(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Delete)]


def stops(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Stop)]


# --- never raises ----------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    pytest.param("$(" * 4000, id="deep-dollar-paren"),
    pytest.param("(" * 2000 + ")" * 2000, id="deep-paren"),
    pytest.param('"$(' * 3000, id="deep-dquote-sub"),
    pytest.param("`" * 1000, id="many-backticks"),
    pytest.param("bash -c " * 200 + "rm -rf x", id="deep-bash-c"),
    pytest.param("rm -rf " + "a/" * 20000, id="huge-path"),
    pytest.param(";" * 5000 + "rm -rf x", id="many-semicolons"),
])
def test_actions_never_raises_on_pathological_input(command):
    act(command)
    act(command, tool="PowerShell", windows=True)


def test_a_non_string_command_yields_nothing():
    assert act(None) == []
    assert act(123) == []


# --- a recognised delete/stop with nothing readable is opaque --------------------------------

def test_a_piped_delete_or_stop_target_is_opaque():
    assert any(None in a.targets for a in deletes("Get-ChildItem x | Remove-Item -Recurse",
                                                   tool="PowerShell", windows=True))
    assert stops("Get-Process foo | Stop-Process", tool="PowerShell", windows=True)[0].opaque
    assert stops("ps -ef | grep x | kill")[0].opaque


def test_a_delete_with_only_flags_is_opaque():
    # Remove-Item fed from the pipeline, only switches given: must not look like a safe no-op
    assert any(None in a.targets for a in deletes("Remove-Item -Recurse -Force",
                                                   tool="PowerShell", windows=True))


# --- PowerShell switches are not targets -----------------------------------------------------

def test_powershell_switches_are_not_targets():
    d = deletes("Remove-Item -Recurse -Force C:/work/wt", tool="PowerShell", windows=True)[0]
    assert d.targets == ("C:/work/wt",)
    d = deletes("Remove-Item -Path:C:/work/wt -Recurse", tool="PowerShell", windows=True)[0]
    assert d.targets == ("C:/work/wt",)


# --- control flow and eval are read ----------------------------------------------------------

@pytest.mark.parametrize("command", [
    "for d in a b; do rm -rf /work/wt; done",
    "if true; then rm -rf /work/wt; fi",
    "while read x; do rm -rf /work/wt; done",
    "eval 'rm -rf /work/wt'",
    "! rm -rf /work/wt",
    "time rm -rf /work/wt",
])
def test_control_flow_and_eval_do_not_hide_a_delete(command):
    assert any(isinstance(a, Delete) for a in act(command)), command


# --- git option before the subcommand --------------------------------------------------------

def test_a_git_option_before_worktree_does_not_hide_it():
    assert any(a.rule_id == "git-worktree-remove"
               for a in act("git -C /other worktree remove /work/wt") if isinstance(a, Delete))


# --- kill signal vs pid ----------------------------------------------------------------------

def test_a_signal_option_is_not_read_as_a_pid():
    assert stops("kill -9 123")[0].pids == (123,)
    assert stops("kill -2 123")[0].pids == (123,)
    assert stops("kill -s KILL 123")[0].pids == (123,) and not stops("kill -s KILL 123")[0].opaque
    # a liveness probe (signal 0) stops nothing (#395 unit 2b)
    assert stops("kill -0 123") == []


# --- nested substitution, any depth ----------------------------------------------------------

def test_a_delete_nested_two_levels_deep_is_found():
    assert any(isinstance(a, Delete) and a.targets == ("/work/wt",)
               for a in act("echo $(echo $(rm -rf /work/wt))"))


# --- python / node bodies: strings and comments do not fire ----------------------------------

@pytest.mark.parametrize("command", [
    "python -c \"print('os.remove is dangerous')\"",
    "python -c \"import pathlib; print(pathlib.Path('.').resolve())\"",
    "python -c \"import signal; print(signal.SIGINT)\"",
    "python -c \"# shutil.rmtree in a comment\"",
    "node -e \"console.log('rmSync note')\"",
    "python -c \"x=[1,2]; x.remove(2)\"",
])
def test_a_delete_or_stop_word_inside_a_code_string_or_comment_does_not_fire(command):
    assert not any(isinstance(a, (Delete, Stop)) for a in act(command)), command


def test_a_real_delete_in_a_python_body_still_fires():
    assert deletes("python -c \"import shutil; shutil.rmtree('/work/wt')\"")[0].targets == ("/work/wt",)
    assert stops("python -c \"import os; os.kill(321, 9)\"")[0].pids == (321,)


# --- redirections are not targets ------------------------------------------------------------

def test_redirections_do_not_become_targets():
    d = deletes("rm -rf /work/wt > log 2>&1")[0]
    assert d.targets == ("/work/wt",)
    # a leading redirect does not hide the command
    assert deletes("2>/dev/null rm -rf /work/wt")[0].targets == ("/work/wt",)


# --- cd to an unreadable place is None, never a crash ----------------------------------------

def test_cd_to_an_unreadable_place_makes_cwd_none():
    assert deletes("cd $UNSET && rm -rf wt")[0].cwd is None
    assert deletes("cd $(pick) && rm -rf wt")[0].cwd is None
