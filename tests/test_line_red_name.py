"""A required check name red on main's head is red on main, as a test id is: the batch is not halved (#414)."""

import pytest
from fake_github import FAILED, PASSED, REPO
from line_world import line_world
from required_names import LINUX_CHECK, WINDOWS_CHECK
from test_merge_batch import line, make_suite, passes

from orch_it.merge import marks


def main_head(hub):
    return hub.branches[hub.main_branch][-1]


def red_on_every_tree(world, place, name):
    """`name` is red on the batch's tree whichever members it holds, and on main's head."""

    hub, _, prs, _ = world
    place.red_names = {name: set(prs.values())}
    hub.checks_on(main_head(hub), FAILED, names=(name,))


@pytest.mark.parametrize("tickets", [(10, 11, 12, 13), (10,)])
def test_a_name_red_on_mains_head_and_a_green_batch_halve_nothing_and_send_no_member_back(tmp_path, tickets):
    world = line_world(tmp_path, tickets=tickets)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    red_on_every_tree(world, place, WINDOWS_CHECK)
    said = {ticket: len(hub.comments(REPO, ticket)) for ticket in tickets}
    done = line(world, tmp_path, suite)
    assert passes(store) == 1  # one pass of the batch: no halving
    assert done.left == 0 and done.merged == 0 and hub.advances == [] and done.reason == "next_run"
    assert [(s.pr, s.result, s.detail) for s in done.served] == [(prs[n], "bound", "main_red") for n in tickets]
    assert done.notes == (f"main is red on {WINDOWS_CHECK}",)
    for ticket in tickets:  # nothing is sent back: no comment on a ticket, the ready mark stays
        assert len(hub.comments(REPO, ticket)) == said[ticket]
        assert marks.ready_since(marks.of_layer(hub.pull_comments(REPO, prs[ticket]), REPO.split("/")[0])) is not None


def test_a_name_red_on_the_batch_and_not_on_mains_head_still_splits(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    place.red_names = {WINDOWS_CHECK: {prs[12]}}
    hub.checks_on(main_head(hub), PASSED, names=(WINDOWS_CHECK,))  # main is green on it
    done = line(world, tmp_path, suite)
    assert passes(store) > 1 and done.left == 1
    assert ci.merged == [prs[10], prs[11], prs[13]]


def test_a_name_main_has_no_result_for_is_not_red_on_main_and_the_batch_splits(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    place.red_names = {WINDOWS_CHECK: {prs[12]}}  # nothing is posted on main's head
    done = line(world, tmp_path, suite)
    assert passes(store) > 1 and done.left == 1 and ci.merged == [prs[10], prs[11], prs[13]]


def test_a_batch_red_on_two_names_with_main_red_on_only_one_splits_as_before(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    place.red_names = {WINDOWS_CHECK: {prs[12]}, LINUX_CHECK: {prs[12]}}
    hub.checks_on(main_head(hub), FAILED, names=(WINDOWS_CHECK,))
    done = line(world, tmp_path, suite)
    assert passes(store) > 1  # the whole batch is red on a name main is green on: it splits to the end
    assert ci.merged == [prs[10], prs[11], prs[13]] and done.left == 1 and hub.pull(REPO, prs[12]).state == "open"
    assert [s.result for s in done.served if s.pr == prs[12]] == ["left"] and done.notes == ()
