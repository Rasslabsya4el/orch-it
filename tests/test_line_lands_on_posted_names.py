"""The batch landing under a base whose protection counts commit statuses and not the check runs of a manual (dispatch)
run on a batch branch: right before the landing the line posts PASSED for each required name it saw green only as a
check run, so the base's sha is the tested tree. It posts nothing for a name that is not green on the tip."""

import pytest
from fake_github import DEFAULT_BRANCH, PASSED, REQUIRED
from line_world import line_world
from test_line_post_after_own_pass import line as line_of
from test_line_post_after_own_pass import unprotected_own_world
from test_merge_batch import line, make_suite, passes

from orch_it.merge.follow import SHORT_SHA
from orch_it.testruns.github import GithubError


def world_counting_statuses_only(tmp_path, tickets=(10, 11, 12, 13)):
    world = line_world(tmp_path, tickets=tickets)
    world[0].counts_check_runs = False  # the protection of the base counts a status, not a dispatch run's check run
    return world


def posts(hub):
    return [(call[1], call[2], call[3]) for call in hub.calls if call[0] == "post_status"]


def test_a_pass_green_as_check_runs_only_lands_the_batch_after_the_line_posts_the_names_as_statuses(tmp_path):
    world = world_counting_statuses_only(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite)
    tip = place.tested[0][0]
    assert passes(store) == 1 and done.notes == () and done.reason == "empty"
    assert hub.advances == [tip] and hub.branches[DEFAULT_BRANCH][-1] == tip  # the base is the tested tree
    assert ci.merged == [prs[n] for n in (10, 11, 12, 13)]
    assert posts(hub) == [(tip, name, PASSED) for name in REQUIRED]  # each name once, on the tested sha only


def test_a_name_the_line_posted_after_an_own_pass_is_not_posted_a_second_time(tmp_path):
    w = unprotected_own_world(tmp_path)
    hub, store, prs, ci = w.world
    hub.required_by_base = {DEFAULT_BRANCH: set(w.required)}
    hub.counts_check_runs = False
    line_of(w, tmp_path)
    tip = w.place.tested[0][0]
    assert hub.advances == [tip] and ci.merged == [prs[10]]
    assert [(call[1], call[2]) for call in hub.calls if call[0] == "post_status"] == [(tip, name) for name in w.required]


def test_a_required_name_red_as_a_check_run_posts_nothing(tmp_path):
    world = world_counting_statuses_only(tmp_path, tickets=(10,))
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    place.red_names = {REQUIRED[0]: {prs[10]}}
    done = line(world, tmp_path, suite)
    assert posts(hub) == [] and hub.advances == [] and ci.merged == [] and [s.result for s in done.served] == ["left"]


def test_a_required_name_pending_or_without_a_check_run_posts_nothing_and_lands_nothing(tmp_path):
    for leave in ("pending", "missing"):
        world = world_counting_statuses_only(tmp_path / leave, tickets=(10,))
        hub, store, prs, ci = world
        suite, place = make_suite(world, tmp_path / leave)
        if leave == "pending":
            place.pending = {REQUIRED[0]}
            ci.minutes = 10_000  # CI never turns it green
        else:
            place.unposted_for = {REQUIRED[0]: {prs[10]}}
        done = line(world, tmp_path / leave, suite)
        tip = place.tested[0][0]
        assert posts(hub) == [] and hub.advances == [] and ci.merged == [], leave
        assert done.notes == (f"required checks not green on {tip[:SHORT_SHA]}: {REQUIRED[0]}",), leave


def test_a_green_check_run_on_another_sha_posts_nothing_on_the_tip(tmp_path):
    world = world_counting_statuses_only(tmp_path, tickets=(10,))
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    place.posted = []  # the pass leaves nothing on the tested tip
    hub.checks_on("elsewhere", PASSED)  # the names are green on a commit that is not the tip
    done = line(world, tmp_path, suite)
    assert posts(hub) == [] and hub.advances == [] and done.notes and "not green" in done.notes[0]


@pytest.mark.parametrize("fails_at", [0, 1])
def test_a_post_that_fails_holds_the_batch_for_the_next_run_and_names_the_error(tmp_path, fails_at):
    world = world_counting_statuses_only(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    post_status, count = hub.post_status, []

    def failing(repo, sha, name, state, description):
        count.append(name)
        if len(count) > fails_at:
            raise GithubError("gh_failed", "HTTP 500")
        post_status(repo, sha, name, state, description)

    hub.post_status = failing
    done = line(world, tmp_path, suite)
    assert hub.advances == [] and ci.merged == [] and done.reason == "next_run"
    assert {s.result for s in done.served} == {"bound"} and all(s.detail == "checks_missing" for s in done.served)
    [note] = done.notes
    assert "gh_failed" in note and REQUIRED[fails_at] in note
