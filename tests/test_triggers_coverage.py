"""Coverage gaps the brain's outside probe found in the trigger (#395 unit 2b). Each test asserts
the ACTION `triggers.actions` yields (targets, opaque, rule id), never the guard's verdict: row 1
cases yield an action naming the path ("/work/other", another worktree) or an opaque one; row 2
cases yield nothing, or a Delete whose only target is not a protected path ("/work/self" is the
session's own worktree)."""

import base64

import pytest

from orch_it.guards import shell, triggers
from orch_it.guards.triggers import Delete, Move, Stop, actions

OTHER = "/work/other"


def act(command, tool="Bash", cwd="/work/self", env=None, windows=False):
    return actions(command, tool=tool, cwd=cwd, env=env or {}, windows=windows)


def deletes(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Delete)]


def stops(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Stop)]


def moves(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Move)]


def named(command, rule, **kw):
    """The Delete or Move of `rule` that `command` yields, with its objects."""
    for a in act(command, **kw):
        if getattr(a, "rule_id", None) == rule:
            return a
    raise AssertionError(f"no {rule} from {command!r}: {act(command, **kw)}")


def objects(action):
    return action.sources if isinstance(action, Move) else action.targets


# --- 1. an unreadable head is opaque, never silent -------------------------------------------

def test_an_unreadable_head_becomes_an_opaque_delete_over_its_arguments():
    found = deletes(f"$(echo rm) -rf {OTHER}")
    assert len(found) == 1 and OTHER in found[0].targets
    assert found[0].rule_id == "unreadable-command"
    assert found[0].recursive and found[0].walk_links and found[0].roots_too


def test_an_ifs_spliced_head_is_opaque_and_keeps_the_unsplit_word():
    found = [a for a in deletes(f"rm${{IFS}}-rf${{IFS}}{OTHER}") if a.rule_id == "unreadable-command"]
    assert found and None in found[0].targets


def test_an_unreadable_head_with_an_unreadable_argument_keeps_it_as_none():
    found = [a for a in deletes("$(pick) $OTHERDIR") if a.rule_id == "unreadable-command"]
    assert found and found[0].targets == (None,)


def test_pure_option_flags_are_not_targets_of_an_unreadable_head():
    found = [a for a in deletes(f"$(echo rm) -rf -- {OTHER}") if a.rule_id == "unreadable-command"]
    assert found[0].targets == (OTHER,)


def test_a_computed_interpreter_running_a_script_passes_as_a_non_protected_target():
    found = [a for a in deletes("$(which python) script.py") if a.rule_id == "unreadable-command"]
    assert len(found) == 1 and found[0].targets == ("script.py",)


def test_a_readable_head_that_is_no_trigger_stays_silent():
    assert act("ls -la /work/other") == []
    assert [a for a in act("ls $(echo x)") if a.rule_id == "unreadable-command"] == []


def test_nesting_past_the_depth_bound_is_opaque_not_silent():
    deep = "echo " + "$(echo " * (shell.MAX_DEPTH + 5) + "rm -rf /work/other" + ")" * (shell.MAX_DEPTH + 5)
    found = [a for a in act(deep) if isinstance(a, Delete)]
    assert any(a.rule_id == "unreadable-command" or OTHER in a.targets for a in found)
    assert any(a.rule_id == "unreadable-command" for a in found)
    wrapped = "(" * (shell.MAX_DEPTH + 5) + "rm -rf /work/other" + ")" * (shell.MAX_DEPTH + 5)
    assert any(a.rule_id == "unreadable-command" for a in deletes(wrapped))


# --- 5. false fires --------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "rm --help", "mv --help", "pkill --help", "kill --help", "rmdir --help", "rm -h",
    "killall --help", "rsync --help", "shred --help", "git worktree remove --help",
])
def test_a_trigger_head_asking_for_help_yields_nothing(command):
    assert act(command) == []


@pytest.mark.parametrize("command", ["Remove-Item -?", "del /?", "taskkill /?", "rd /?"])
def test_a_windows_help_flag_yields_nothing(command):
    assert act(command, tool="PowerShell", windows=True) == []


def test_help_beside_a_real_target_of_a_non_help_flag_still_fires():
    assert deletes(f"rsync -h --delete /e/ {OTHER}/")  # `-h` is human-readable there, not help


def test_kill_l_lists_signals_and_kill_0_probes_so_neither_stops():
    assert stops("kill -l") == []
    assert stops("kill -l 9") == []
    assert stops("kill -0 123") == []
    assert stops("kill -s 0 123") == []
    assert stops("kill -9 123")[0].pids == (123,)


def test_a_posix_command_word_is_case_sensitive():
    assert act(f"RM -rf {OTHER}") == []
    assert act(f"Rm -rf {OTHER}") == []
    assert act(f"KILL 123") == []
    assert deletes(f"rm -rf {OTHER}")[0].targets == (OTHER,)


def test_a_windows_command_word_is_case_insensitive():
    assert deletes(f"RM -rf {OTHER}", tool="PowerShell", windows=True)[0].rule_id == "rm"
    assert deletes(f"REMOVE-ITEM -Recurse {OTHER}", tool="PowerShell", windows=True)
    assert deletes(f"RM -rf {OTHER}", windows=True)  # Git Bash on Windows


# --- 3. case, select and function bodies are read --------------------------------------------

def test_every_case_arm_command_is_read():
    out = deletes(f"case $x in a) rm {OTHER}/y ;; b) rm {OTHER}/z ;; esac")
    assert [d.targets for d in out] == [(f"{OTHER}/y",), (f"{OTHER}/z",)]


def test_case_arms_on_their_own_lines_and_with_patterns_are_read():
    command = f"case \"$1\" in\n  start|go) echo hi ;;\n  (clean) rm -rf {OTHER} ;;\n  *) rm -rf /work/self/tmp ;;\nesac"
    assert [d.targets for d in deletes(command)] == [(OTHER,), ("/work/self/tmp",)]


def test_a_case_arm_with_a_stop_is_read():
    assert stops("case x in a) kill 123 ;; esac")[0].pids == (123,)


def test_a_case_that_runs_nothing_dangerous_yields_nothing():
    assert act("case $x in a) echo rm ;; *) ls ;; esac") == []


def test_a_select_loop_body_is_read():
    assert deletes(f"select f in a b; do rm -rf {OTHER}; done")[0].targets == (OTHER,)


def test_a_function_body_is_read():
    assert deletes(f"function clean {{ rm -rf {OTHER}; }}; clean")[0].targets == (OTHER,)
    assert deletes(f"clean() {{ rm -rf {OTHER}; }}")[0].targets == (OTHER,)
    assert deletes(f"clean () {{\n rm -rf {OTHER}\n}}")[0].targets == (OTHER,)


@pytest.mark.parametrize("command", [
    "case", "case x in", "case x in a)", "case x in a) ;; esac", "case ) in", "function", "function f",
    "function f {", "f() {", "select", "select x in", "case x in ( ) rm", "a) rm",
])
def test_a_malformed_case_select_or_function_never_raises(command):
    act(command)
    act(command, tool="PowerShell", windows=True)
