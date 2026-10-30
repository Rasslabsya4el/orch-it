"""The line posts the required names a passed suite covered, whatever place ran it (#414 unit 23, brain decision 5, item 7).

A green batch was held for ever on a repo whose base protects nothing, with Actions on: the place rule picked `own`, the
suite passed on the batch tip, but the line posted the names only when GitHub's runs would not start, so no name was
ever green on the tip. The line is the holder there (it merges only with every required name green, from whichever place
ran it), so after a pass at any place but GitHub it posts the names that run covered: its own OS's jobs and the OS-free
names. It posts nothing after a failure, nothing for a name the run did not cover (a job of another OS stays unposted and
holds the batch, `guards status` names it), and nothing after a pass at GitHub (GitHub posts its own check runs)."""

import contextlib

import pytest
from fake_github import PASSED, REPO
from required_names import LINUX_CHECK, LINUX_OS, WINDOWS_CHECK, WINDOWS_OS
from test_github_off_and_private import SCAN_CHECK, batch_world, line, seed
from test_line_os_free_names import waiting
from test_merge_batch import FixturePlace

from orch_it.guards.status import Waiting
from orch_it.merge.batch import place_rule_suite
from orch_it.merge.follow import SHORT_SHA
from orch_it.testruns.config import TestSettings
from orch_it.testruns.github import WORKFLOW_FILE
from orch_it.testruns.places import PlaceEnd


def unprotected_own_world(tmp_path, *, required=(LINUX_CHECK, SCAN_CHECK), github_minutes=9.0, **kwargs):
    """A private-looking repo with Actions on and no branch protection, whose place rule picks `own` (measured faster)."""

    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=required, **kwargs)
    w.actions.enabled = True
    hub, store, prs, ci = w.world
    hub.required_by_base = {}  # nothing on GitHub holds the required names: only the line does
    w.place.id = "own"
    seed(store, "own", 5.0)
    seed(store, "github", github_minutes)
    return w


def test_a_pass_at_own_on_an_unprotected_repo_posts_the_covered_names_and_the_batch_merges(tmp_path):
    w = unprotected_own_world(tmp_path)
    hub, store, prs, ci = w.world
    done = line(w, tmp_path)
    tip = w.place.tested[0][0]
    assert {c.name: c.state for c in hub.checks(REPO, tip)} == {LINUX_CHECK: PASSED, SCAN_CHECK: PASSED}
    assert ci.merged == [prs[10]] and hub.advances == [tip] and done.reason == "empty"
    # every post went to the batch tip, none to main's head or to a member's head (the member's own CI checks are not posts)
    assert [(call[1], call[2]) for call in hub.calls if call[0] == "post_status"] == [
        (tip, LINUX_CHECK), (tip, SCAN_CHECK)]
    assert hub.checks(REPO, "m0") == []


def test_a_failing_suite_at_own_posts_nothing_and_merges_nothing(tmp_path):
    w = unprotected_own_world(tmp_path)
    hub, store, prs, ci = w.world
    w.place.red = {prs[10]}
    done = line(w, tmp_path)
    assert hub.checks(REPO, w.place.tested[0][0]) == []
    assert ci.merged == [] and hub.advances == [] and [s.result for s in done.served] == ["left"]


def test_a_pass_at_github_leaves_the_posting_to_github(tmp_path):
    w = unprotected_own_world(tmp_path)
    hub, store, prs, ci = w.world
    settings = TestSettings(github_tests=True, required_checks=w.required)
    at_github = FixturePlace(hub, set())
    at_github.id, at_github.posted = "github", []  # a pass at GitHub posts its own check runs; this fake posts none

    @contextlib.contextmanager
    def checkout(sha):
        tree = tmp_path / "trees-github" / sha
        (tree / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        (tree / "tip.txt").write_text(sha, "utf-8")
        (tree / ".github" / "workflows" / WORKFLOW_FILE).write_text(
            f"name: tests\njobs:\n  j:\n    name: {LINUX_CHECK}\n    runs-on: {LINUX_OS}-latest\n", "utf-8")
        yield tree

    w.suite = place_rule_suite(store, REPO, settings, checkout, places={"github": at_github})
    done = line(w, tmp_path)
    tip = at_github.tested[0][0]
    assert hub.checks(REPO, tip) == []
    assert ci.merged == [] and hub.advances == [] and done.reason == "next_run"


class GithubOnPlaceholders:
    """The github place with a stub for private data in the repo: it posts its own check run for the Linux job only and its
    report flags the one test that read the stub, so the suite does not settle there (`run_tests`, placeholder rerun)."""

    id, os = "github", LINUX_OS

    def __init__(self, hub):
        self.hub = hub

    def run(self, request, scratch):
        tip = (request.work_tree / "tip.txt").read_text("utf-8")
        self.hub.checks_on(tip, PASSED, names=(LINUX_CHECK,))
        report = scratch / "junit.xml"
        report.write_text("<testsuites><testsuite name='pytest'><testcase classname='tests.test_cart' name='test_ok'>"
                          "<properties><property name='reads_placeholder' value='1'/></properties></testcase>"
                          "</testsuite></testsuites>", "utf-8")
        return PlaceEnd(report, False, False, 0, 0.0, 1.0)


def test_a_placeholder_rerun_at_own_posts_nothing_and_the_github_result_stays_the_one_that_decides(tmp_path):
    w = unprotected_own_world(tmp_path, github_minutes=1.0)  # github is measured fastest: it runs the suite first
    hub, store, prs, ci = w.world
    github = GithubOnPlaceholders(hub)
    w.suite = place_rule_suite(store, REPO, TestSettings(github_tests=True, required_checks=w.required),
                               w.checkout, places={"own": w.place, "github": github})
    done = line(w, tmp_path)
    [(tip, _)] = w.place.tested  # the rerun of the one placeholder test ran at own, after GitHub's pass
    assert w.place.id_runs == [(tip, ["tests.test_cart::test_ok"])]
    assert {c.name: c.state for c in hub.checks(REPO, tip)} == {LINUX_CHECK: PASSED}  # GitHub's own, nothing from the line
    assert not [call for call in hub.calls if call[0] == "post_status"]
    assert ci.merged == [] and done.notes == (f"required checks not green on {tip[:SHORT_SHA]}: {SCAN_CHECK}",)


def test_an_own_pass_leaves_a_name_of_another_os_unposted_and_the_status_names_it(tmp_path, monkeypatch):
    w = unprotected_own_world(tmp_path, required=(LINUX_CHECK, WINDOWS_CHECK))
    hub, store, prs, ci = w.world
    (tmp_path / "status").mkdir()
    done = line(w, tmp_path)
    tip = w.place.tested[0][0]
    assert {c.name: c.state for c in hub.checks(REPO, tip)} == {LINUX_CHECK: PASSED}
    assert ci.merged == [] and hub.advances == [] and hub.branches["main"] == ["m0"]
    assert done.notes == (f"required checks not green on {tip[:SHORT_SHA]}: {WINDOWS_CHECK}",)
    assert waiting(tmp_path / "status", monkeypatch, required=(LINUX_CHECK, WINDOWS_CHECK)) == (
        Waiting(WINDOWS_CHECK, WINDOWS_OS),)


@pytest.mark.parametrize("protected", [True, False])
def test_a_pass_at_own_with_github_healthy_posts_for_a_protected_base_too(tmp_path, protected):
    w = unprotected_own_world(tmp_path)
    hub, store, prs, ci = w.world
    if protected:
        hub.required_by_base = {"main": set(w.required)}
    line(w, tmp_path)
    assert ci.merged == [prs[10]]
