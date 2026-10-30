"""Test runs tied to their PR (#406 decision 1, row 1): every `test_run` start the runner writes for a PR head
carries that `pr`, the `part` it covers (its selection), the head's `commit` and the PR's ticket in the envelope; a
later head of the same PR gets its own `commit`, and the runs of the earlier head stay counted at their own commit.
A run started with no PR keeps `pr` None.

Seam, fixed by the spec (no session to agree it with): `run_tests` with `pr` and `ticket`, forced to the own place and
to a stub machine place (the fake ssh of test_machine_runs.py), so both runs of a head take the same attempt path.
"""

import sys
from pathlib import Path

from machine_repos import GREEN, Repos, git

from orch_it.events.store import Store
from orch_it.testruns import environment
from orch_it.testruns.config import Machine, TestSettings
from orch_it.testruns.machine import MachinePlace
from orch_it.testruns.places import OwnPlace
from orch_it.testruns.runner import run_tests

REPO = "acme-x/widgets-y"
FAKE_SSH = (sys.executable, str(Path(__file__).with_name("fake_ssh.py")))
BOX = "machine:box"
PR = 7
TICKET = 406


def fixture(tmp_path):
    repos = Repos(tmp_path, GREEN)
    box = Machine("box", "fakehost", environment.this_os(), str(tmp_path / "box-checkout"), python=sys.executable)
    settings = TestSettings(machines=(box,))
    places = {"own": OwnPlace(), BOX: MachinePlace(box, origin_url=str(repos.origin), ssh=FAKE_SSH)}
    return repos, Store.open(tmp_path / "store"), places, settings


def run_at(store, repos, settings, places, place, **options):
    return run_tests(store, work_tree=repos.caller, repo=REPO, settings=settings, places=places, place=place,
                     **options)


def starts(store):
    """(place, ticket, pr, part, commit) of every `test_run` start in the store."""

    return store.query(
        "SELECT place, ticket, json_extract(detail, '$.pr'), json_extract(detail, '$.part'),"
        " json_extract(detail, '$.commit') FROM events"
        " WHERE kind = 'step.started' AND step = 'test_run'").rows


def test_each_run_of_a_pr_head_is_tied_to_its_pr_part_commit_and_ticket(tmp_path):
    repos, store, places, settings = fixture(tmp_path)
    for place in ("own", BOX):
        run_at(store, repos, settings, places, place, pr=PR, ticket=TICKET)
    assert sorted(starts(store)) == sorted([
        ("own", TICKET, PR, "full", repos.old),
        (BOX, TICKET, PR, "full", repos.old),
    ])


def test_a_later_head_of_the_pr_gets_its_own_commit_and_the_earlier_count_stays(tmp_path):
    repos, store, places, settings = fixture(tmp_path)
    for place in ("own", BOX):
        run_at(store, repos, settings, places, place, pr=PR, ticket=TICKET)
    git(repos.caller, "checkout", "--detach", repos.new)
    for place in ("own", BOX):
        run_at(store, repos, settings, places, place, pr=PR, ticket=TICKET)
    rows = starts(store)
    assert sum(1 for row in rows if row[4] == repos.old) == 2
    assert sum(1 for row in rows if row[4] == repos.new) == 2
    assert {row[2] for row in rows} == {PR}


def test_a_run_started_with_no_pr_keeps_pr_none(tmp_path):
    repos, store, places, settings = fixture(tmp_path)
    run_at(store, repos, settings, places, "own")
    assert [(place, pr) for place, _ticket, pr, _part, _commit in starts(store)] == [("own", None)]
