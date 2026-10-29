"""The remote test-slot lock (#418): a create refused while the same name is being removed is read as busy.

Windows refuses a create that races a removal of the same name with PermissionError, where the other
platforms say the file exists; the lock must read both the same way, inside its own wait.
"""

import multiprocessing
import os

from orch_it.testruns import remote

CONTENDERS = 8
ROUNDS = 50
STALE_SECONDS = 3600.0
WAIT_SECONDS = 60  # a barrier or queue read that waits this long means a broken harness, not a slow one


def _refused(path: str) -> PermissionError:
    return PermissionError(13, "refused while the same name is being removed", path)


def test_a_create_refused_once_during_a_racing_removal_is_busy_and_the_lock_is_taken(tmp_path, monkeypatch):
    path = str(tmp_path / ("slot" + remote.LOCK_SUFFIX))
    real_open = os.open
    refusals = [1]  # one refusal: the lock's second try is the one that takes the lock

    def refuse_once_then_open(name, flags, *args, **kwargs):
        if refusals[0] > 0:
            refusals[0] -= 1
            raise _refused(name)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(remote.os, "open", refuse_once_then_open)
    assert remote._lock(path, STALE_SECONDS) is True
    assert os.path.exists(path)


def test_a_create_refused_on_every_try_ends_as_a_held_lock_does_not_as_a_permission_error(tmp_path, monkeypatch):
    path = str(tmp_path / ("slot" + remote.LOCK_SUFFIX))

    def always_refused(name, flags, *args, **kwargs):
        raise _refused(name)

    monkeypatch.setattr(remote.os, "open", always_refused)
    assert remote._lock(path, STALE_SECONDS) is False


def _contender(path: str, rounds: int, barrier, results) -> None:
    for round_no in range(rounds):
        barrier.wait(WAIT_SECONDS)  # every contender starts the round at once
        try:
            outcome = "holder" if remote._lock(path, STALE_SECONDS) else "busy"
        except PermissionError:
            outcome = "PermissionError"
        except Exception as e:  # noqa: BLE001 -- any other failure is counted, not hidden
            outcome = f"error:{type(e).__name__}"
        results.put((round_no, outcome))
        barrier.wait(WAIT_SECONDS)  # every try of the round is in
        if outcome == "holder":
            remote._unlink(path)  # the module's own release
            results.put((round_no, "released" if not os.path.exists(path) else "not_released"))
        barrier.wait(WAIT_SECONDS)  # released before the next round starts


def test_contenders_for_one_slot_lock_leave_one_holder_and_seven_busy_per_round(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    path = str(tmp_path / ("slot" + remote.LOCK_SUFFIX))
    barrier = ctx.Barrier(CONTENDERS)
    results = ctx.Queue()
    workers = [ctx.Process(target=_contender, args=(path, ROUNDS, barrier, results))
               for _ in range(CONTENDERS)]
    for worker in workers:
        worker.start()
    outcomes: dict[int, list[str]] = {}
    try:
        for _ in range(ROUNDS * (CONTENDERS + 1)):  # one outcome per contender and one release per round
            round_no, outcome = results.get(timeout=WAIT_SECONDS)
            outcomes.setdefault(round_no, []).append(outcome)
    finally:
        for worker in workers:
            worker.join(WAIT_SECONDS)
            if worker.is_alive():
                worker.terminate()
    per_round = [outcomes.get(round_no, []) for round_no in range(ROUNDS)]
    holders = {o.count("holder") for o in per_round}
    busy = {o.count("busy") for o in per_round}
    permission = sum(o.count("PermissionError") for o in per_round)
    released = sum(o.count("released") for o in per_round)
    print(f"rounds={ROUNDS} holders_per_round={sorted(holders)} busy_per_round={sorted(busy)} "
          f"permission_errors={permission} released={released}")
    assert permission == 0
    assert holders == {1}
    assert busy == {CONTENDERS - 1}
    assert released == ROUNDS
