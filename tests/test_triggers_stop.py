"""The stop commands (#395 decision 3; row 5 forms) and cwd tracking and expansion read by the
trigger. Each stop form is recognised and its pids, name/pattern matchers, tree flag and
unreadable-target flag read; a wait is never a stop."""

import pytest

from orch_it.guards.triggers import Delete, Move, Stop, actions


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def act(command, tool="Bash", cwd="/work", env=None, windows=False):
    return actions(command, tool=tool, cwd=cwd, env=env or {}, windows=windows)


def stops(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Stop)]


def deletes(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Delete)]


STOP_WAYS = [
    ("kill 123", "Bash", False, "kill"),
    ("kill -9 123", "Bash", False, "kill"),
    ("kill -s TERM 123", "Bash", False, "kill"),
    ("pkill python", "Bash", False, "pkill"),
    ("pkill -f runserver", "Bash", False, "pkill"),
    ("killall -9 node", "Bash", False, "killall"),
    ("xargs kill < pids.txt", "Bash", False, "kill"),
    ("python -c \"import os; os.kill(123, 9)\"", "Bash", False, "python-os-kill"),
    ("node -e \"process.kill(123)\"", "Bash", False, "node-process-kill"),
    ("taskkill /PID 123", "PowerShell", True, "taskkill"),
    ("taskkill /IM python.exe /T", "PowerShell", True, "taskkill"),
    ("taskkill /F /PID 123", "Bash", True, "taskkill"),
    ("tskill 123", "PowerShell", True, "tskill"),
    ("Stop-Process -Id 123", "PowerShell", True, "stop-process"),
    ("Stop-Process -Name python", "PowerShell", True, "stop-process"),
    ("spps -Name python", "PowerShell", True, "stop-process"),
    ("wmic process where processid=123 delete", "PowerShell", True, "wmic-terminate"),
    ("Invoke-CimMethod -ClassName Win32_Process -MethodName Terminate", "PowerShell", True, "cim-terminate"),
]


@pytest.mark.parametrize("command,tool,windows,rule", STOP_WAYS)
def test_every_stop_way_is_a_trigger(command, tool, windows, rule):
    found = [a for a in stops(command, tool=tool, windows=windows) if a.rule_id == rule]
    assert found, f"no {rule} stop from {command!r}: got {act(command, tool=tool, windows=windows)}"


def test_a_pid_stop_names_the_pid():
    assert stops("kill 123")[0].pids == (123,)
    assert stops("taskkill /PID 456", tool="PowerShell", windows=True)[0].pids == (456,)
    assert stops("Stop-Process -Id 7,8,9", tool="PowerShell", windows=True)[0].pids == (7, 8, 9)


def test_a_name_or_pattern_stop_carries_a_matcher():
    pk = stops("pkill -f runserver")[0]
    assert pk.matchers[0].kind == "cmdline_regex" and pk.matchers[0].text == "runserver"
    ka = stops("killall node")[0]
    assert ka.matchers[0].kind == "name_glob" and ka.matchers[0].text == "node"
    tk = stops("taskkill /IM python.exe", tool="PowerShell", windows=True)[0]
    assert tk.matchers[0].kind == "name_glob" and tk.matchers[0].text == "python.exe"


def test_a_tree_stop_is_marked():
    assert stops("taskkill /T /PID 1", tool="PowerShell", windows=True)[0].tree is True
    neg = stops("kill -- -42")[0]
    assert neg.tree is True and 42 in neg.pids


def test_an_unreadable_stop_target_is_opaque():
    assert stops("kill $(cat pid)")[0].opaque is True
    assert stops("kill $PID")[0].opaque is True
    assert stops('taskkill /FI "IMAGENAME eq p*"', tool="PowerShell", windows=True)[0].opaque is True


def test_a_wait_is_never_a_stop():
    assert act("wait") == []
    assert act("wait 123") == []
    assert act("Wait-Process -Id 123", tool="PowerShell", windows=True) == []
    assert act("Wait-Process -Name python", tool="PowerShell", windows=True) == []


def test_cd_before_a_delete_sets_its_directory():
    assert deletes("cd /work/sub && rm -rf wt")[0].cwd == "/work/sub"


def test_a_cd_inside_a_subshell_does_not_leak_out():
    out = deletes("(cd /elsewhere); rm -rf wt", cwd="/work")
    assert out[-1].cwd == "/work"


def test_an_assignment_expands_in_a_later_target():
    assert deletes('W=/work/wt; rm -rf "$W"')[0].targets == ("/work/wt",)


def test_tilde_and_env_expand():
    assert deletes("rm -rf ~/wt", env={"HOME": "/work/me"})[0].targets == ("/work/me/wt",)


def test_a_substitution_inside_a_command_contributes_its_own_delete():
    out = act("echo $(rm -rf /work/wt)")
    assert any(isinstance(a, Delete) and a.targets == ("/work/wt",) for a in out)


def test_the_segment_is_bounded_and_never_raises():
    assert len(deletes("rm -rf " + "/x" * 200)[0].segment) <= 120
    for bad in ("", "'", '"', "$(", "`", "rm -rf '", "|||", "&&&", "rm \\", "cmd /c"):
        act(bad)
        act(bad, tool="PowerShell", windows=True)


def test_an_xargs_fed_delete_or_stop_is_opaque():
    # the target comes from stdin the guard cannot read, so the action must mark it unreadable
    d = deletes("echo /work/wt | xargs rm -rf")
    assert d and None in d[-1].targets
    s = stops("cat pids | xargs kill -9")
    assert s and s[-1].opaque is True
