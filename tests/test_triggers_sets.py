"""The closed sets of the trigger's rule ids and matcher kinds stay in step with what `actions`
produces, and the ways hardened after review are read (#395 unit 2)."""

import pytest
from orch_it.guards import triggers
from orch_it.guards.triggers import Delete, Move, Stop, actions


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def act(command, tool="Bash", cwd="/work", env=None, windows=False):
    return actions(command, tool=tool, cwd=cwd, env=env or {}, windows=windows)


def deletes(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Delete)]


def stops(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Stop)]


_CASES = [
    "rm -rf x", "rmdir x", "del /s x", "unlink x", "Remove-Item -Recurse x",
    "find x -delete", "find x -exec rm {} +", "rsync --delete a/ b/", "robocopy a b /MIR",
    "git worktree remove x", "git worktree prune", "git worktree move a b",
    "python -c \"shutil.rmtree('x')\"", "node -e \"fs.rmSync('x')\"",
    "mv a b", "move a b", "Move-Item a b", "ren a b", "Rename-Item a b",
    "kill 1", "pkill x", "killall x", "taskkill /PID 1", "tskill 1", "Stop-Process -Id 1",
    "wmic process where processid=1 delete", "Invoke-CimMethod -MethodName Terminate",
    "python -c \"import os;os.kill(1,9)\"", "node -e \"process.kill(1)\"",
]


def test_every_rule_id_and_matcher_kind_is_in_its_closed_set():
    seen_rules, seen_kinds = set(), set()
    for command in _CASES:
        win = any(w in command for w in ("Remove-Item", "Move-Item", "Rename-Item", "robocopy",
                                         "Stop-Process", "wmic", "Invoke-CimMethod", "taskkill",
                                         "tskill"))
        tool = "PowerShell" if win else "Bash"
        for action in act(command, tool=tool, windows=win):
            seen_rules.add(action.rule_id)
            for matcher in getattr(action, "matchers", ()):
                seen_kinds.add(matcher.kind)
    assert seen_rules, "the cases produced no action"
    assert seen_rules <= set(triggers.RULE_IDS), seen_rules - set(triggers.RULE_IDS)
    assert seen_kinds <= set(triggers.MATCHER_KINDS)


def test_more_ways_after_hardening():
    assert deletes("python3.12 -c \"import shutil; shutil.rmtree('/work/wt')\"")[0].targets == ("/work/wt",)
    assert deletes("rsync -a --del /e/ /work/wt/")[0].rule_id == "rsync-delete"
    assert deletes("find -L /work/wt -delete")[0].targets == ("/work/wt",)
    assert stops("taskkill //F //PID 123", tool="PowerShell", windows=True)[0].pids == (123,)
    assert stops("pkill -9f runserver")[0].matchers[0].kind == "cmdline_regex"
    d = deletes("Remove-Item $env:TEMP/wt -Recurse", tool="PowerShell", windows=True,
                env={"TEMP": "C:/t"})[0]
    assert d.targets == ("C:/t/wt",)


def test_move_destination_is_not_a_source():
    assert list(act("mv -t /dest /work/wt")[0].sources) == ["/work/wt"]
    mv = act("Move-Item -Destination C:/x -Path C:/work/wt", tool="PowerShell", windows=True)[0]
    assert mv.sources == ("C:/work/wt",)
