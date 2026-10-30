"""A required check tied to no OS (#414 unit 16, brain decision 5): with GitHub out, the place that ran the suite posts it
with the names of its own OS, so only the OS-bound names of another OS hold a batch, and `guards status` names which
check waits for a place of which OS.

An OS-free name is a required name that is no job of the tree's `tests` workflow (a scan of the pull request, the
commit e-mail check: they run where the suite runs). A job with an OS's runner is OS-bound; a job whose runner names no
OS is neither covered nor OS-free (the run cannot say it covered it)."""

import json

import pytest
from fake_github import FAILED, PASSED, REPO
from required_names import LINUX_CHECK, LINUX_OS, MACOS_OS, WINDOWS_CHECK, WINDOWS_OS
from test_github_off_and_private import CAUSES, SCAN_CHECK, batch_world, line, seed, workflow_of

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.guards import status as guards_status
from orch_it.merge.follow import SHORT_SHA
from orch_it.testruns import github
from orch_it.testruns.github import WORKFLOW_FILE
from orch_it.testruns.github_place import checks_on_os, os_free_checks

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


def test_a_job_whose_runner_names_no_os_is_not_posted_for_the_run_cannot_say_it_covered_it(tmp_path):
    jobs_text = "name: tests\njobs:\n  " + f"{LINUX_CHECK}:\n    name: {LINUX_CHECK}\n    runs-on: {LINUX_OS}-latest\n" \
                f"  {WINDOWS_CHECK}:\n    name: {WINDOWS_CHECK}\n    runs-on: ${{{{ matrix.runner }}}}\n"
    assert os_free_checks(jobs_text, (LINUX_CHECK, WINDOWS_CHECK, SCAN_CHECK)) == (SCAN_CHECK,)
    assert checks_on_os(jobs_text, (LINUX_CHECK, WINDOWS_CHECK, SCAN_CHECK), LINUX_OS) == (LINUX_CHECK,)


# --- the classification ----------------------------------------------------------------------------------------

def test_os_free_names_are_the_required_names_that_are_no_job_of_the_workflow():
    text = workflow_of({LINUX_CHECK: LINUX_OS, WINDOWS_CHECK: WINDOWS_OS})
    required = (SCAN_CHECK, WINDOWS_CHECK, EMAIL_CHECK, LINUX_CHECK)
    assert os_free_checks(text, required) == (SCAN_CHECK, EMAIL_CHECK)


def test_a_job_is_found_by_its_key_when_it_has_no_name():
    text = f"name: tests\njobs:\n  {LINUX_CHECK}:\n    runs-on: {LINUX_OS}-latest\n"
    assert os_free_checks(text, (LINUX_CHECK, SCAN_CHECK)) == (SCAN_CHECK,)


def test_a_workflow_with_no_job_or_none_at_all_makes_no_name_os_free():
    assert os_free_checks("", (SCAN_CHECK,)) == ()
    assert os_free_checks("name: tests\n", (SCAN_CHECK,)) == ()


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
    assert waiting(tmp_path, monkeypatch) == (f"{WINDOWS_CHECK} waits for a {WINDOWS_OS} place",)


def test_status_names_one_line_per_waiting_name(tmp_path, monkeypatch):
    jobs = JOBS | {SCAN_CHECK: MACOS_OS}
    assert waiting(tmp_path, monkeypatch, jobs=jobs) == (f"{WINDOWS_CHECK} waits for a {WINDOWS_OS} place",
                                                         f"{SCAN_CHECK} waits for a {MACOS_OS} place")


def test_status_says_nothing_waits_when_a_machine_that_is_on_has_the_os(tmp_path, monkeypatch):
    assert waiting(tmp_path, monkeypatch, machines=[(WINDOWS_OS, True)]) == ()


def test_a_machine_that_is_off_covers_no_os(tmp_path, monkeypatch):
    assert waiting(tmp_path, monkeypatch, machines=[(WINDOWS_OS, False)]) == (
        f"{WINDOWS_CHECK} waits for a {WINDOWS_OS} place",)


def test_the_own_os_covers_its_names_and_an_os_free_name_never_waits(tmp_path, monkeypatch):
    assert waiting(tmp_path, monkeypatch, own_os=WINDOWS_OS) == (f"{LINUX_CHECK} waits for a {LINUX_OS} place",)
    assert waiting(tmp_path, monkeypatch, required=(SCAN_CHECK,)) == ()


def test_the_command_prints_the_waiting_lines_after_the_table(tmp_path, monkeypatch, capsys):
    store, tree = status_world(tmp_path, monkeypatch, required=REQUIRED, jobs=JOBS)
    monkeypatch.setattr(github, "run_gh", lambda args: json.dumps({}))
    monkeypatch.setattr(guards_status, "this_os", lambda: LINUX_OS)
    main(["--store", str(tmp_path / "store"), "guards", "status", "--repo", "o/n", "--work-tree", str(tree)])
    out = capsys.readouterr().out.splitlines()
    assert f"{WINDOWS_CHECK} waits for a {WINDOWS_OS} place" in out
