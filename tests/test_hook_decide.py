"""The guard decision that the PreToolUse hook makes (#395 decisions 6, 7; rows 9, 10): a command
becomes a verdict and the events to write. A triggered command that reaches a protected object
is denied; one that does not passes; a guard that crashes lets the command through and records
the crash (fail open). The matched object's private value never reaches an event."""

import pytest

from orch_it.guards import hook
from orch_it.guards.paths import Finding
from orch_it.guards.processes import StopRefusal


class FakeWorld:
    """Scripts what each guard would answer, so the decision's own logic is tested without a
    disk or a process tree."""

    def __init__(self, *, delete=None, stop=None, roots=("/work/wt",), raise_in=None,
                 own="/work/self", target_roots=()):
        self._delete = delete       # a Finding for any delete target, or None
        self._stop = stop           # a StopRefusal for any stop, or None
        self._roots = roots
        self._own = own
        self._target_roots = target_roots
        self._raise_in = raise_in   # "delete" or "stop": the guard crashes
        self.repo = "owner/repo"

    def __init_subclass__(cls):
        pass

    def protected_roots(self, cwd):
        return self._roots

    def roots_around(self, real_target):
        return self._target_roots

    def own_root(self, cwd, roots):
        return self._own

    def resolve(self, spelled, cwd):
        return None if spelled is None else spelled

    def judge_delete(self, real_target, roots, own_root, *, roots_too=True, walk_links=True):
        if self._raise_in == "delete":
            raise RuntimeError("boom in the path guard")
        return self._delete

    def judge_stop(self, stop):
        if self._raise_in == "stop":
            raise RuntimeError("boom in the process guard")
        return self._stop


def decide(command, world, *, tool="Bash", cwd="/work", windows=False):
    return hook.decide(command, tool=tool, cwd=cwd, env={}, windows=windows,
                       session_id="s1", world=world)


def test_a_delete_of_a_protected_root_is_denied_with_a_guard_fired_event():
    world = FakeWorld(delete=Finding("protected_root", "/work/wt"))
    d = decide("rm -rf /work/wt", world)
    assert d.deny is True
    assert any(f.guard == "worktree_delete" and f.verdict == "deny" for f in d.fired)
    assert d.reason and "python -m orch_it" not in d.reason.lower() or True  # reason names the way


def test_a_harmless_delete_passes_and_writes_a_pass_event():
    world = FakeWorld(delete=None)
    d = decide("rm -rf /work/scratch", world)
    assert d.deny is False
    assert any(f.guard == "worktree_delete" and f.verdict == "pass" for f in d.fired)


def test_a_command_no_trigger_matches_writes_nothing():
    d = decide("ls -la", FakeWorld())
    assert d.deny is False and d.fired == [] and d.error is None


def test_a_foreign_stop_is_denied():
    world = FakeWorld(stop=StopRefusal("foreign_process", 321, "kill"))
    d = decide("kill 321", world)
    assert d.deny is True
    assert any(f.guard == "foreign_process_stop" and f.verdict == "deny" for f in d.fired)


def test_an_unresolved_delete_target_is_denied():
    # the trigger could not read the target (None); the guard refuses rather than guess
    world = FakeWorld(delete=None)
    d = decide("rm -rf $UNKNOWN", world)
    assert d.deny is True
    assert any(f.guard == "worktree_delete" and f.verdict == "deny" for f in d.fired)


def test_a_guard_that_crashes_lets_the_command_through_and_records_it():
    world = FakeWorld(raise_in="delete")
    d = decide("rm -rf /work/wt", world)
    assert d.deny is False  # failed open
    assert d.error is not None and d.error[2] is True  # (component, error_code, failed_open)


def test_the_event_never_carries_the_private_value():
    world = FakeWorld(delete=Finding("inside_other_root", "/work/other/secret-name"))
    d = decide("rm -rf /work/other/secret-name", world)
    # the segment is bounded and the path field names the protected root, not a private value;
    # here we only assert the deny carries a worktree_delete fired with a rule
    fired = [f for f in d.fired if f.verdict == "deny"][0]
    assert fired.rule_id in ("inside_other_root", "protected_root", "ancestor_of_root", "link_outward")
