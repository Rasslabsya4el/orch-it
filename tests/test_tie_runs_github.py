"""The GitHub side of #406 decision 1 (acceptance row 1): a check run on one PR's head is a `test_run` pair tied to that
PR (`pr`, `part` = the job's name, `commit` = the run's head, the PR's ticket in the envelope). The pair's start is
written when the job starts, its end when the job completes, once each (idempotent on re-import). A run that is not
of exactly one PR's head keeps `pr` None: a push, a schedule, a hand-started (`workflow_dispatch`) run, or a run of
several PRs (#406 decision 1, "a run that is not one PR's head"). `part` is the job's name for every GitHub run. One AST
scan finds every function of src/orch_it that names the `test_run` step, and it must be a registered writer.

Seams, fixed by the spec: `run_tests` (own place and a stub machine, as tests/test_tie_runs.py) and `import_github`
over the fake `gh` of tests/test_github_import.py, both writing one store.
"""

import ast
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from orch_it.events.ids import import_id
from orch_it.events.store import Store
from orch_it.testruns import github
from orch_it.testruns.config import required_checks
from orch_it.testruns.runner import run_tests
from test_github_import import NOW, REPO, FakeGh, ends_of_test_runs
from test_tie_runs import BOX, fixture as tie_fixture, run_at, starts

pytestmark = pytest.mark.os_touching  # processes and git checkouts, as the two modules it builds on (#414 unit 4)

PR = 7
TICKET = 406
LINUX, WINDOWS = required_checks({}, REPO)  # the two job names the fixture's setting names; never literals
SRC = Path(__file__).resolve().parents[1] / "src" / "orch_it"
IN_PROGRESS = {"status": "in_progress", "conclusion": None, "completed": None}  # a job still running, its end not yet

#: every function of src/orch_it that names the `test_run` step (path under src/orch_it, qualified function name):
#: the registered writers of a `test_run` event. A new writer must be added here, and it must tie its starts.
REGISTERED = {
    ("testruns/runner.py", "_Run.refused"),
    ("testruns/runner.py", "_Run.attempt"),
    ("testruns/github.py", "_Importer._target_events"),
    ("testruns/github.py", "_Importer._start"),
}

PLANTED = '''
def plant_start(writer, envelope):
    writer.write(envelope | {"kind": "step.started", "step": "test_run", "detail": {"place": "github", "pr": 7}})
'''


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


@pytest.fixture
def gh(tmp_path):
    folder = tmp_path / "gh"
    folder.mkdir()
    fake = FakeGh(folder)
    fake.pull_bodies[PR] = f"Refs #{TICKET}"  # the PR's ticket: the reader of `merge/follow.py ticket_of` finds it
    fake.issues[TICKET] = {"number": TICKET, "title": "tie", "body": "", "labels": [], "state": "open"}
    return fake


def import_all(store, gh):
    return github.import_github(store, REPO, gh=gh, now=NOW)


def start_rows(store):
    """(run, ticket, detail) of every `test_run` start in the store."""

    return [(run, ticket, json.loads(detail)) for run, ticket, detail in store.query(
        "SELECT run, ticket, detail FROM events WHERE kind = 'step.started' AND step = 'test_run'").rows]


def tie_gaps(store, *, pr_head: bool) -> list:
    """The starts that break the tie. `pr_head` is the scenario's flag: True, every run is one PR's head and each start
    must carry `pr`, `part`, `commit` and the ticket; False (a push), the start must carry no `pr` at all."""

    gaps = []
    for run, ticket, detail in start_rows(store):
        if pr_head:
            missing = [name for name, value in (("pr", detail.get("pr")), ("part", detail.get("part")),
                                                ("commit", detail.get("commit")), ("ticket", ticket)) if not value]
            if missing:
                gaps.append((run, missing))
        elif detail.get("pr") is not None:
            gaps.append((run, ["pr set on a run that is not a PR head"]))
    return gaps


def _functions(node, prefix=""):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield from _functions(child, f"{prefix}{child.name}.")
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield f"{prefix}{child.name}", child


def writers(root: Path) -> set:
    """(path under `root`, qualified function) of every function whose code names the `test_run` step."""

    found = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, func in _functions(tree):
            if any(isinstance(node, ast.Constant) and node.value == "test_run" for node in ast.walk(func)):
                found.add((path.relative_to(root).as_posix(), name))
    return found


def unregistered(root: Path) -> set:
    return writers(root) - REGISTERED


# --- row 1: one PR head at three places ----------------------------------------------------------

def test_one_pr_head_tested_at_three_places_gives_four_tied_pairs(tmp_path, gh):
    repos, store, places, settings = tie_fixture(tmp_path)
    for place in ("own", BOX):
        run_at(store, repos, settings, places, place, pr=PR, ticket=TICKET)
    gh.add_run(100, sha=repos.old, pr=PR)
    report = import_all(store, gh)
    assert report.runs_failed == 0 and report.failure_codes == []
    assert sorted(starts(store)) == sorted([
        ("own", TICKET, PR, "full", repos.old),
        (BOX, TICKET, PR, "full", repos.old),
        ("github", TICKET, PR, LINUX, repos.old),
        ("github", TICKET, PR, WINDOWS, repos.old),
    ])
    assert tie_gaps(store, pr_head=True) == []


def test_a_later_head_is_tied_to_its_own_commit_and_the_earlier_head_keeps_its_own(tmp_path, gh):
    repos, store, places, settings = tie_fixture(tmp_path)
    gh.add_run(100, sha=repos.old, pr=PR)
    gh.add_run(101, sha=repos.new, pr=PR, created=600)  # the PR's newer head, run later
    import_all(store, gh)
    by_commit = Counter(detail["commit"] for _run, _ticket, detail in start_rows(store))
    assert by_commit == {repos.old: 2, repos.new: 2}
    assert {run.split("/")[0] for run, _t, detail in start_rows(store) if detail["commit"] == repos.new} == {
        "github:101"}
    assert tie_gaps(store, pr_head=True) == []


# --- the start is written when the job starts, once ----------------------------------------------

def test_an_in_progress_check_run_gets_its_start_once_and_its_end_on_completion(tmp_path, gh):
    store = Store.open(tmp_path / "store")
    gh.add_run(100, status="in_progress", conclusion=None, pr=PR, **{LINUX: IN_PROGRESS, WINDOWS: IN_PROGRESS})
    import_all(store, gh)
    assert sorted(run.split("/")[-2] for run, _t, _d in start_rows(store)) == sorted(
        [str(100 * 10 + 1), str(100 * 10 + 2)])
    assert ends_of_test_runs(store) == 0
    before = store.query("SELECT count(*) FROM events").rows
    import_all(store, gh)  # a second import of the same in-progress run writes nothing
    assert store.query("SELECT count(*) FROM events").rows == before
    gh.runs.clear(), gh.jobs.clear(), gh.artifacts.clear(), gh.timing.clear()
    gh.add_run(100, pr=PR)  # the run completes: the same two jobs, now with their ends
    import_all(store, gh)
    assert len(start_rows(store)) == 2  # one start per job, never a second one on completion
    assert ends_of_test_runs(store) == 2
    assert tie_gaps(store, pr_head=True) == []


# --- a push is not a PR head: its `pr` stays None (exempt by the scenario flag) ------------------

def test_a_push_run_is_not_a_pr_head_and_its_starts_keep_pr_none(tmp_path, gh):
    store = Store.open(tmp_path / "store")
    gh.add_run(200, event="push", pr=None, branch="main", sha="push-sha")
    import_all(store, gh)
    rows = start_rows(store)
    assert len(rows) == 2 and [detail.get("pr") for _run, _t, detail in rows] == [None, None]
    assert sorted(detail.get("part") for _run, _t, detail in rows) == sorted([LINUX, WINDOWS])
    assert tie_gaps(store, pr_head=False) == []


# --- the starter guard: every writer of a `test_run` start is registered --------------------------

def test_every_function_that_names_the_test_run_step_is_a_registered_writer():
    assert unregistered(SRC) == set()


def test_a_planted_starter_in_a_copy_is_outside_the_registry(tmp_path):
    copy = tmp_path / "orch_it"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "testruns" / "planted.py").write_text(PLANTED, encoding="utf-8")
    assert unregistered(copy) == {("testruns/planted.py", "plant_start")}


def test_a_planted_pr_start_without_its_part_is_reported_by_the_tie_check(tmp_path):
    store = Store.open(tmp_path / "store")
    envelope = {"repo": REPO, "ticket": TICKET, "role": "github", "session": None, "parent_session": None,
                "run": "github:999/1/1", "note": None, "step": "test_run"}
    detail = {"place": "github", "chosen": "github", "reason": None, "run_os": "linux", "commit": "x",
              "branch": "feat/x", "pr": PR, "selection": "full", "event": "pull_request"}  # no `part`
    event = github._Importer._event("step.started", import_id(github.SOURCE, "planted/1/1", "step.started"),
                                    NOW, envelope, detail)
    with store.writer() as writer:
        writer.write(event)
    assert tie_gaps(store, pr_head=True) == [("github:999/1/1", ["part"])]


def test_a_refused_start_of_a_pr_head_still_names_its_pr_part_commit_and_ticket(tmp_path):
    """#406 decision 1, `_Run.refused` -> `_started`: no place takes the PR's head, so the run ends refused and its
    start is written with no place. That start still carries the PR, the part (the run's selection), the head's
    commit and the PR's ticket in its envelope; the tie check finds no gap in it."""
    repos, store, _places, settings = tie_fixture(tmp_path)
    done = run_tests(store, work_tree=repos.caller, repo=REPO, settings=settings, places={}, pr=PR,
                     ticket=TICKET, wait_minutes=0, sleep=lambda seconds: None)
    assert done.outcome == "refused"
    assert starts(store) == [(None, TICKET, PR, "full", repos.old)]
    assert tie_gaps(store, pr_head=True) == []


# --- #406 decision 1: only a pull_request run of exactly one PR is tied to that PR -----------------

def pr_numbers_called(gh):
    """The calls that read a pull request of this repo (the ticket reader's pull read)."""
    return [call for call in gh.calls if call[-1].endswith(f"/pulls/{PR}")]


@pytest.mark.parametrize("event", ["workflow_dispatch", "schedule"])
def test_a_hand_started_or_scheduled_run_is_not_tied_to_a_pr_it_names(tmp_path, gh, event):
    """GitHub fills `pull_requests` for a dispatch on a PR's branch too: that run is no PR's head (pr None, no
    ticket read), its part is still the job's name."""
    store = Store.open(tmp_path / "store")
    gh.add_run(200, event=event, pr=PR, branch="feat/x")
    import_all(store, gh)
    rows = start_rows(store)
    assert len(rows) == 2
    assert [detail["pr"] for _run, _ticket, detail in rows] == [None, None]
    assert [ticket for _run, ticket, _detail in rows] == [None, None]
    assert sorted(detail["part"] for _run, _ticket, detail in rows) == sorted([LINUX, WINDOWS])
    assert pr_numbers_called(gh) == []
    assert tie_gaps(store, pr_head=False) == []


def test_a_pull_request_run_of_two_prs_is_tied_to_neither(tmp_path, gh):
    store = Store.open(tmp_path / "store")
    gh.add_run(201, pr=PR)
    gh.runs[-1]["pull_requests"].append({"number": PR + 1})
    import_all(store, gh)
    assert [detail["pr"] for _run, _ticket, detail in start_rows(store)] == [None, None]
    assert tie_gaps(store, pr_head=False) == []


def test_a_pull_request_run_of_one_pr_is_tied_to_it(tmp_path, gh):
    store = Store.open(tmp_path / "store")
    gh.add_run(202, event="pull_request", pr=PR)
    import_all(store, gh)
    assert [detail["pr"] for _run, _ticket, detail in start_rows(store)] == [PR, PR]
    assert tie_gaps(store, pr_head=True) == []


# --- #406 decision 1: a failed read of the PR's ticket costs the run no pair ------------------------

def test_a_failed_read_of_the_prs_ticket_still_writes_the_pair_with_no_ticket(tmp_path, gh):
    store = Store.open(tmp_path / "store")
    gh.add_run(100, pr=PR)
    gh.fail_when = lambda args: any(arg.endswith(f"/pulls/{PR}") for arg in args)
    report = import_all(store, gh)
    assert report.runs_failed == 0  # the run itself was read and written
    assert report.ticket_reads_failed == 1 and report.failure_codes == ["gh_failed"]
    rows = start_rows(store)
    assert sorted((detail["part"], ticket, detail["pr"]) for _run, ticket, detail in rows) == sorted(
        [(LINUX, None, PR), (WINDOWS, None, PR)])
    assert ends_of_test_runs(store) == 2


# --- #406 decision 5: every gh call of the import, the ticket reads included, is counted -------------

def test_every_gh_call_of_a_pr_run_import_is_counted_in_the_request_budget(tmp_path, gh, monkeypatch):
    """`run_gh` is the importer's default caller: each call it makes is one `github.called` event. The ticket reader
    of a PR with no `Refs` line reads the pull, the parked issues and the parked search; none may go uncounted."""
    monkeypatch.setenv("ORCH_IT_STORE", str(tmp_path / "store"))
    monkeypatch.setattr(github, "_call_gh", gh)  # the real `run_gh` (and its count) over the fake answers
    gh.pull_bodies.pop(PR)  # no Refs line: the ticket reader searches the parked issues too
    gh.add_run(100, pr=PR)
    store = Store.open(tmp_path / "store")
    report = github.import_github(store, REPO, now=NOW)
    assert report.runs_failed == 0
    assert any("search/issues" in call for call in gh.calls)  # the parked search was asked
    assert store.query("SELECT count(*) FROM events WHERE kind = 'github.called'").rows == [(len(gh.calls),)]


# --- #406 decision 1: every registered starter ties the start it writes for a PR head -----------

def own_attempt(tmp_path, gh):
    repos, store, places, settings = tie_fixture(tmp_path)
    run_at(store, repos, settings, places, "own", pr=PR, ticket=TICKET)
    return store


def refused(tmp_path, gh):
    repos, store, _places, settings = tie_fixture(tmp_path)
    done = run_tests(store, work_tree=repos.caller, repo=REPO, settings=settings, places={}, pr=PR,
                     ticket=TICKET, wait_minutes=0, sleep=lambda seconds: None)
    assert done.outcome == "refused"
    return store


def completed_import(tmp_path, gh):
    store = Store.open(tmp_path / "store")
    gh.add_run(100, sha="sha-pr", pr=PR)
    import_all(store, gh)
    return store


def in_progress_import(tmp_path, gh):
    store = Store.open(tmp_path / "store")
    gh.add_run(100, status="in_progress", conclusion=None, pr=PR, **{LINUX: IN_PROGRESS, WINDOWS: IN_PROGRESS})
    import_all(store, gh)
    return store


#: one driver per registered starter: it writes a PR head's `test_run` start through that starter only.
#: `_Importer._start` is reached through the in-progress path (`_started_events`), `_Importer._target_events` through
#: a completed run; `_Run.attempt` through an own run, `_Run.refused` through a run no place takes.
STARTERS = {
    ("testruns/runner.py", "_Run.attempt"): own_attempt,
    ("testruns/runner.py", "_Run.refused"): refused,
    ("testruns/github.py", "_Importer._target_events"): completed_import,
    ("testruns/github.py", "_Importer._start"): in_progress_import,
}


def test_the_drivers_cover_every_registered_starter():
    assert set(STARTERS) == REGISTERED


@pytest.mark.parametrize("starter", sorted(STARTERS))
def test_a_registered_starter_ties_each_start_it_writes_for_a_pr_head(tmp_path, gh, starter):
    store = STARTERS[starter](tmp_path, gh)
    assert start_rows(store), f"{starter} wrote no test_run start"
    assert tie_gaps(store, pr_head=True) == []


def test_a_starter_that_leaves_part_empty_fails_the_tie_check(tmp_path, gh, monkeypatch):
    """The planted starter: the importer's start written without `part` (the old `_start`, minus the job name). The
    same driver that passes above reports the gap."""
    original = github._Importer._start

    def without_part(self, *args, **kwargs):
        event = original(self, *args, **kwargs)
        event["detail"] = {key: value for key, value in event["detail"].items() if key != "part"}
        return event

    monkeypatch.setattr(github._Importer, "_start", without_part)
    store = STARTERS[("testruns/github.py", "_Importer._start")](tmp_path, gh)
    assert [missing for _run, missing in tie_gaps(store, pr_head=True)] == [["part"], ["part"]]
