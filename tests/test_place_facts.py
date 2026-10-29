"""The place rule's inputs read from the store (#394 decision 5): live runs per place (a
`test_run` started with no end, younger than its bound plus a grace) and measured durations
(the run times of the last 10 ended passed or failed runs of the same repo and selection, the
start overheads of the place's last 10 ended runs)."""

from datetime import UTC, datetime, timedelta

from orch_it.events.store import Store
from orch_it.testruns.place_rule import LIVE_GRACE_MINUTES, Measured, read_facts

REPO = "acme-x/widgets-y"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def at(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


class Runs:
    def __init__(self, tmp_path):
        self.store = Store.open(tmp_path / "store")
        self.writer = self.store.writer()
        self.n = 0

    def add(self, place, *, ago=30, outcome="passed", run_minutes=5.0, start_minutes=1.0,
            repo=REPO, selection="full", run_os="linux", ended=True, bound=15, machine=None):
        self.n += 1
        envelope = {"repo": repo, "ticket": None, "role": "script", "session": None,
                    "parent_session": None, "turn": None, "step": "test_run", "run": f"r{self.n}",
                    "note": None}
        if machine:
            envelope["machine"] = machine
        self.writer.write(envelope | {"kind": "step.started", "at": at(ago), "detail": {
            "place": place, "selection": selection, "run_os": run_os, "bound_minutes": bound}})
        if ended:
            self.writer.write(envelope | {"kind": "step.ended", "at": at(ago - 1), "detail": {
                "outcome": outcome, "reason": "red" if outcome == "failed" else None,
                "run_minutes": run_minutes, "start_minutes": start_minutes}})

    def facts(self, selection="full", need_os="any", repo=REPO):
        return read_facts(self.store, repo=repo, selection=selection, need_os=need_os, now=NOW)


def test_a_started_run_with_no_end_inside_its_bound_and_grace_is_live(tmp_path):
    runs = Runs(tmp_path)
    runs.add("own", ago=5, ended=False)
    runs.add("machine:box", ago=15 + LIVE_GRACE_MINUTES - 1, ended=False)
    runs.add("machine:box", ago=2, ended=False, repo="someone/else")  # a machine serves every repo
    runs.add("machine:box", ago=15 + LIVE_GRACE_MINUTES + 1, ended=False)  # its writer died long ago
    runs.add("github", ago=3, ended=True)
    live, _ = runs.facts()
    assert live == {"own": 1, "machine:box": 2}


def test_a_run_with_a_longer_bound_stays_live_longer(tmp_path):
    runs = Runs(tmp_path)
    runs.add("own", ago=50, ended=False, bound=60)
    assert runs.facts()[0] == {"own": 1}


def test_run_times_are_the_last_ten_passed_or_failed_runs_of_the_repo_and_selection(tmp_path):
    runs = Runs(tmp_path)
    for n in range(12):  # oldest first: 100, 101, ..., 111 minutes; the two oldest fall out
        runs.add("own", ago=500 - n, run_minutes=100.0 + n)
    runs.add("own", ago=20, outcome="failed", run_minutes=1.0)
    runs.add("own", ago=10, outcome="no_result", run_minutes=999.0)
    runs.add("own", ago=10, selection="ids:1:abc", run_minutes=999.0)
    runs.add("own", ago=10, repo="someone/else", run_minutes=999.0)
    _, measured = runs.facts()
    assert sorted(measured["own"].runs) == [1.0] + [100.0 + n for n in range(3, 12)]


def test_start_overheads_are_the_place_s_last_ten_ended_runs_of_any_repo(tmp_path):
    runs = Runs(tmp_path)
    for n in range(11):
        runs.add("github", ago=300 - n, start_minutes=float(n), repo="someone/else", selection="x")
    runs.add("github", ago=5, outcome="no_result", start_minutes=50.0)
    _, measured = runs.facts()
    assert sorted(measured["github"].starts) == [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 50.0]
    assert measured["github"].runs == ()


def test_a_run_needing_one_os_measures_only_runs_on_that_os(tmp_path):
    runs = Runs(tmp_path)
    runs.add("github", run_minutes=3.0, run_os="linux")
    runs.add("github", run_minutes=9.0, run_os="windows")
    assert runs.facts(need_os="windows")[1]["github"].runs == (9.0,)
    assert sorted(runs.facts(need_os="any")[1]["github"].runs) == [3.0, 9.0]


def test_an_empty_store_gives_no_live_run_and_no_measurement(tmp_path):
    assert Runs(tmp_path).facts() == ({}, {})


def test_absurd_numbers_in_the_store_are_no_measurement_and_no_bound(tmp_path):
    runs = Runs(tmp_path)
    runs.add("own", ago=5, ended=False, bound=1e300)  # read as the default bound: still live
    runs.add("github", run_minutes=1e308, start_minutes=10 ** 400)
    runs.add("github", run_minutes=3.0, start_minutes=1.0)
    live, measured = runs.facts()
    assert live == {"own": 1}
    assert measured["github"] == Measured((1.0,), (3.0,))


def test_a_start_dated_in_the_future_is_no_live_run(tmp_path):
    runs = Runs(tmp_path)
    runs.add("own", ago=-2 * 24 * 60, ended=False)
    runs.add("own", ago=-(LIVE_GRACE_MINUTES - 1), ended=False)  # a little clock skew is live
    assert runs.facts()[0] == {"own": 1}


def test_another_machine_s_own_runs_are_neither_live_here_nor_measured_here(tmp_path):
    runs = Runs(tmp_path)
    runs.add("own", ago=5, ended=False, machine="ffffffffffffffff")
    runs.add("own", ago=30, run_minutes=99.0, machine="ffffffffffffffff")
    runs.add("own", ago=40, run_minutes=4.0)
    runs.add("machine:box", ago=5, ended=False, machine="ffffffffffffffff")  # a shared place
    live, measured = runs.facts()
    assert live == {"machine:box": 1}
    assert measured["own"].runs == (4.0,)
