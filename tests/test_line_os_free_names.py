"""A required check tied to no OS (#414 unit 16, brain decision 5): with GitHub out, the place that ran the suite posts it
with the names of its own OS, so only the OS-bound names of another OS hold a batch, and `guards status` names which
check waits for a place of which OS.

An OS-free name is a required name that is no job of the tree's `tests` workflow (a scan of the pull request, the
commit e-mail check: they run where the suite runs), and only in a workflow that can be read whole: it has a job, and
every job has a literal name (or key) and a runner that names an OS. A workflow with a job named or run on by an
expression (`${{ matrix.os }}`), a reusable workflow or an unknown runner is unclassifiable: no name is OS-free then,
every name no job matches stays unposted and holds the batch (fail closed)."""

import json

import pytest
from fake_github import FAILED, PASSED, REPO
from required_names import LINUX_CHECK, LINUX_OS, MACOS_OS, WINDOWS_CHECK, WINDOWS_OS
from test_github_off_and_private import CAUSES, SCAN_CHECK, batch_world, line, seed, workflow_of

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.guards import status as guards_status
from orch_it.guards.status import Waiting
from orch_it.merge.follow import SHORT_SHA
from orch_it.testruns import github
from orch_it.testruns.github import WORKFLOW_FILE
from orch_it.testruns.github_place import checks_on_os, classify_checks, os_free_checks

EMAIL_CHECK = "commit-email-of-another-kind"  # a second required name that is no job of the workflow


# --- the line: GitHub out, the suite passes at the own place ---------------------------------------------------

@pytest.mark.parametrize("cause", CAUSES)
def test_an_os_free_name_and_the_own_os_name_are_both_posted_and_the_batch_merges(tmp_path, cause):
    w = batch_world(tmp_path, cause, tickets=(10,), required=(LINUX_CHECK, SCAN_CHECK, EMAIL_CHECK))
    hub, store, prs, ci = w.world
    done = line(w, tmp_path)
    tip = w.place.tested[0][0]
    assert {c.name: c.state for c in hub.checks(REPO, tip)} == {
        LINUX_CHECK: PASSED, SCAN_CHECK: PASSED, EMAIL_CHECK: PASSED}
    assert ci.merged == [prs[10]] and hub.advances == [tip] and done.reason == "empty"


def test_an_os_free_name_is_posted_and_only_the_name_of_the_other_os_holds_the_batch(tmp_path):
    required = (LINUX_CHECK, WINDOWS_CHECK, SCAN_CHECK)
    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=required)
    hub, store, prs, ci = w.world
    done = line(w, tmp_path)
    tip = w.place.tested[0][0]
    assert {c.name: c.state for c in hub.checks(REPO, tip)} == {LINUX_CHECK: PASSED, SCAN_CHECK: PASSED}
    assert ci.merged == [] and hub.advances == [] and hub.branches["main"] == ["m0"]
    assert done.notes == (f"required checks not green on {tip[:SHORT_SHA]}: {WINDOWS_CHECK}",)
    assert [s.result for s in done.served] == ["bound"] and hub.pull(REPO, prs[10]).state == "open"


def test_a_failed_suite_posts_no_name_not_even_an_os_free_one(tmp_path):
    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=(LINUX_CHECK, SCAN_CHECK))
    hub, store, prs, ci = w.world
    w.place.red = {prs[10]}
    done = line(w, tmp_path)
    assert ci.merged == [] and hub.advances == [] and [s.result for s in done.served] == ["left"]
    assert hub.checks(REPO, w.place.tested[0][0]) == []


def test_with_github_not_out_the_line_posts_no_name_os_free_or_not(tmp_path):
    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=(LINUX_CHECK, SCAN_CHECK))
    w.actions.enabled = True  # Actions is on: the names are GitHub's to post
    hub, store, prs, ci = w.world
    w.place.id = "own"
    seed(store, "own", 5.0)
    seed(store, "github", 9.0)  # own is measured faster: it is chosen, GitHub is not out
    done = line(w, tmp_path)
    assert ci.merged == [] and hub.advances == [] and done.reason == "next_run"
    assert hub.checks(REPO, hub.branches["main"][-1]) == []


def test_a_name_posted_red_by_somebody_is_left_red_even_when_it_is_os_free(tmp_path):
    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=(LINUX_CHECK, SCAN_CHECK))
    hub, store, prs, ci = w.world
    w.place.posted = [SCAN_CHECK]
    w.place.red_names = {SCAN_CHECK: {prs[10]}}  # the pass itself posted this name red
    done = line(w, tmp_path)
    assert ci.merged == [] and [s.result for s in done.served] == ["left"]
    assert {c.name: c.state for c in hub.checks(REPO, w.place.tested[0][0])} == {
        LINUX_CHECK: PASSED, SCAN_CHECK: FAILED}


MATRIX_JOB = "  matrix-job:\n    name: tests (${{ matrix.os }})\n    runs-on: ${{ matrix.os }}\n"
SIBLINGS = {
    "matrix_named": MATRIX_JOB,
    "reusable_workflow": "  reusable:\n    uses: ./.github/workflows/other.yml\n",
    "runner_group": "  group-runner:\n    name: group-runner\n    runs-on: { group: some-group }\n",
    "unknown_runner": "  own-runner:\n    name: own-runner\n    runs-on: self-hosted\n",
}


def with_job(extra):
    return workflow_of({LINUX_CHECK: LINUX_OS}) + extra


def test_a_matrix_named_windows_job_is_not_posted_green_by_a_linux_pass(tmp_path):
    # the Windows check is a job named by an expression: the workflow cannot say what the other names are tied to
    required = (LINUX_CHECK, WINDOWS_CHECK, SCAN_CHECK)
    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=required, workflow=with_job(MATRIX_JOB))
    hub, store, prs, ci = w.world
    done = line(w, tmp_path)
    tip = w.place.tested[0][0]
    assert {c.name: c.state for c in hub.checks(REPO, tip)} == {LINUX_CHECK: PASSED}
    assert ci.merged == [] and hub.advances == []
    assert done.notes == (f"required checks not green on {tip[:SHORT_SHA]}: {WINDOWS_CHECK}, {SCAN_CHECK}",)


@pytest.mark.parametrize("sibling", list(SIBLINGS.values()), ids=list(SIBLINGS))
def test_an_unclassifiable_sibling_job_leaves_a_name_the_file_lacks_unposted(tmp_path, sibling):
    # the Windows job may live in the file the sibling points at, or be named by an expression
    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=(LINUX_CHECK, WINDOWS_CHECK),
                    workflow=with_job(sibling))
    hub, store, prs, ci = w.world
    done = line(w, tmp_path)
    assert {c.name: c.state for c in hub.checks(REPO, w.place.tested[0][0])} == {LINUX_CHECK: PASSED}
    assert ci.merged == [] and WINDOWS_CHECK in done.notes[0]


def test_a_workflow_with_no_job_posts_no_name_but_the_suite_still_passes(tmp_path):
    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=(LINUX_CHECK, SCAN_CHECK), workflow="name: tests\n")
    hub, store, prs, ci = w.world
    line(w, tmp_path)
    assert hub.checks(REPO, w.place.tested[0][0]) == [] and ci.merged == []


# --- the classification ----------------------------------------------------------------------------------------

def test_os_free_names_are_the_required_names_that_are_no_job_of_the_workflow():
    text = workflow_of({LINUX_CHECK: LINUX_OS, WINDOWS_CHECK: WINDOWS_OS})
    required = (SCAN_CHECK, WINDOWS_CHECK, EMAIL_CHECK, LINUX_CHECK)
    assert os_free_checks(text, required) == (SCAN_CHECK, EMAIL_CHECK)


def test_a_job_is_found_by_its_key_when_it_has_no_name():
    text = f"name: tests\njobs:\n  {LINUX_CHECK}:\n    runs-on: {LINUX_OS}-latest\n"
    assert os_free_checks(text, (LINUX_CHECK, SCAN_CHECK)) == (SCAN_CHECK,)


@pytest.mark.parametrize("extra", list(SIBLINGS.values()), ids=list(SIBLINGS))
def test_a_workflow_with_an_unclassifiable_job_makes_no_name_os_free(extra):
    required = (LINUX_CHECK, WINDOWS_CHECK, SCAN_CHECK)
    text = with_job(extra)
    assert os_free_checks(text, required) == ()
    classes = classify_checks(text, required)
    assert classes.bound == {LINUX_CHECK: LINUX_OS} and classes.free == ()
    assert classes.unclassified == (WINDOWS_CHECK, SCAN_CHECK)


def test_a_fully_classifiable_workflow_classifies_every_required_name():
    classes = classify_checks(workflow_of(JOBS), (WINDOWS_CHECK, SCAN_CHECK, LINUX_CHECK))
    assert classes.bound == {WINDOWS_CHECK: WINDOWS_OS, LINUX_CHECK: LINUX_OS}
    assert classes.free == (SCAN_CHECK,) and classes.unclassified == ()


def test_a_workflow_with_no_job_or_none_at_all_makes_no_name_os_free():
    assert os_free_checks("", (SCAN_CHECK,)) == ()
    assert os_free_checks("name: tests\n", (SCAN_CHECK,)) == ()
    assert classify_checks("", (SCAN_CHECK,)).unclassified == (SCAN_CHECK,)


# --- guards status names the check and the OS it waits for -----------------------------------------------------

def status_world(tmp_path, monkeypatch, *, required, jobs, machines=()):
    settings = {"required_checks": list(required), "test_machines": [
        {"id": f"m-{n}", "host": f"host-{n}", "os": os_name, "checkout": "co", "on": on}
        for n, (os_name, on) in enumerate(machines)]}
    (tmp_path / "settings.json").write_text(json.dumps(settings), "utf-8")
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    tree = tmp_path / "tree"
    (tree / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (tree / ".github" / "workflows" / WORKFLOW_FILE).write_text(workflow_of(jobs), "utf-8")
    return Store.open(tmp_path / "store"), tree


REQUIRED = (LINUX_CHECK, WINDOWS_CHECK, SCAN_CHECK)
JOBS = {LINUX_CHECK: LINUX_OS, WINDOWS_CHECK: WINDOWS_OS}


def waiting(tmp_path, monkeypatch, machines=(), own_os=LINUX_OS, **kwargs):
    store, tree = status_world(tmp_path, monkeypatch, required=kwargs.get("required", REQUIRED),
                               jobs=kwargs.get("jobs", JOBS), machines=machines)
    return guards_status.status(store, "o/n", tree, protection=lambda repo: {}, own_os=own_os).waiting


def test_status_says_which_check_waits_for_a_place_of_which_os(tmp_path, monkeypatch):
    assert waiting(tmp_path, monkeypatch) == (Waiting(WINDOWS_CHECK, WINDOWS_OS),)


def test_status_names_one_entry_per_waiting_name(tmp_path, monkeypatch):
    jobs = JOBS | {SCAN_CHECK: MACOS_OS}
    assert waiting(tmp_path, monkeypatch, jobs=jobs) == (Waiting(WINDOWS_CHECK, WINDOWS_OS),
                                                         Waiting(SCAN_CHECK, MACOS_OS))


def test_status_says_nothing_waits_when_a_machine_that_is_on_has_the_os(tmp_path, monkeypatch):
    assert waiting(tmp_path, monkeypatch, machines=[(WINDOWS_OS, True)]) == ()


def test_a_machine_that_is_off_covers_no_os(tmp_path, monkeypatch):
    assert waiting(tmp_path, monkeypatch, machines=[(WINDOWS_OS, False)]) == (Waiting(WINDOWS_CHECK, WINDOWS_OS),)


def test_the_own_os_covers_its_names_and_an_os_free_name_never_waits(tmp_path, monkeypatch):
    assert waiting(tmp_path, monkeypatch, own_os=WINDOWS_OS) == (Waiting(LINUX_CHECK, LINUX_OS),)
    assert waiting(tmp_path, monkeypatch, required=(SCAN_CHECK,)) == ()


def test_status_names_a_name_the_workflow_cannot_place_as_unclassified(tmp_path, monkeypatch):
    store, tree = status_world(tmp_path, monkeypatch, required=REQUIRED, jobs=JOBS)
    (tree / ".github" / "workflows" / WORKFLOW_FILE).write_text(with_job(MATRIX_JOB), "utf-8")
    report = guards_status.status(store, "o/n", tree, protection=lambda repo: {}, own_os=LINUX_OS)
    assert report.waiting == (Waiting(WINDOWS_CHECK, None), Waiting(SCAN_CHECK, None))


def command_lines(tmp_path, monkeypatch, capsys, text=None):
    store, tree = status_world(tmp_path, monkeypatch, required=REQUIRED, jobs=JOBS)
    if text:
        (tree / ".github" / "workflows" / WORKFLOW_FILE).write_text(text, "utf-8")
    monkeypatch.setattr(github, "run_gh", lambda args: json.dumps({}))
    monkeypatch.setattr(guards_status, "this_os", lambda: LINUX_OS)
    main(["--store", str(tmp_path / "store"), "guards", "status", "--repo", "o/n", "--work-tree", str(tree)])
    return capsys.readouterr().out.splitlines()


def test_the_command_says_a_name_holds_a_batch_only_while_github_does_not_run_it(tmp_path, monkeypatch, capsys):
    rows = command_lines(tmp_path, monkeypatch, capsys)
    [line_] = [row for row in rows if row.startswith(f"{WINDOWS_CHECK}:")]
    assert WINDOWS_OS in line_ and "only GitHub runs" in line_ and "while GitHub does not run it" in line_
    assert not any(row.startswith(f"{LINUX_CHECK}:") or row.startswith(f"{SCAN_CHECK}:") for row in rows)


def test_the_command_says_an_unclassified_name_holds_a_batch_while_github_does_not_run_it(tmp_path, monkeypatch, capsys):
    rows = command_lines(tmp_path, monkeypatch, capsys, with_job(MATRIX_JOB))
    [line_] = [row for row in rows if row.startswith(f"{SCAN_CHECK}:")]
    assert "no OS" in line_ and "while GitHub does not run it" in line_
