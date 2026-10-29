"""The merge line in batches (#414 item 5, row 3, on the fixture world): up to `batch_size` ready members are combined
on one branch from main, the required suite runs once on that tree through the place rule (`run_tests`, whose
`test_run` starts are the passes the store counts), green: main becomes exactly the tested tree and every member ends
merged; red: the batch halves down to the one red member, which goes back to its ticket while the others merge."""

import contextlib
import json

import pytest
from fake_github import REPO
from line_world import line_world

from orch_it import tickets
from orch_it.merge import marks
from orch_it.merge.batch import place_rule_suite
from orch_it.merge.line import run_line
from orch_it.merge.settings import MergeSettings, SettingsError
from orch_it.testruns.config import TestSettings
from orch_it.testruns.places import PlaceEnd

PASS_MINUTES = 5


def failing_id(pr):
    return f"tests.test_cart::test_quote_{pr}"


class FixturePlace:
    """A place with no process: the tree it is given names the batch commit to test (`tip.txt`); the suite is red,
    naming `failing_id(pr)`, for each member of that commit in `red`. Each pass takes `PASS_MINUTES` of the hub's
    clock."""

    id, os = "own", "linux"

    def __init__(self, hub, red):
        self.hub, self.red = hub, red
        self.tested = []  # (tip, outcome) per pass, in order
        self.during = []  # callables run while a pass runs (the world moves under it)

    def run(self, request, scratch):
        tip = (request.work_tree / "tip.txt").read_text("utf-8")
        failing = [pr for pr in self.hub.members_of(tip) if pr in self.red]
        cases = "".join(f"<testcase classname='tests.test_cart' name='test_quote_{pr}'><failure message='x'/></testcase>"
                        for pr in failing)
        report = scratch / "junit.xml"
        report.write_text(f"<testsuites><testsuite name='pytest'><testcase classname='tests.test_cart' name='test_ok'/>"
                          f"{cases}</testsuite></testsuites>", "utf-8")
        self.tested.append((tip, "failed" if failing else "passed"))
        self.hub.later(minutes=PASS_MINUTES)
        for hook in self.during:
            hook()
        return PlaceEnd(report, False, False, 1 if failing else 0, 0.0, PASS_MINUTES * 60.0)


def make_suite(world, tmp_path, red=()):
    hub, store, _, ci = world
    place = FixturePlace(hub, set(red))

    @contextlib.contextmanager
    def checkout(sha):
        tree = tmp_path / "trees" / sha
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "tip.txt").write_text(sha, "utf-8")
        yield tree

    return place_rule_suite(store, REPO, TestSettings(), checkout, places={"own": place}), place


def line(world, tmp_path, suite, **settings):
    hub, store, _, ci = world
    return run_line(hub, store, REPO, "main", settings=MergeSettings(**settings), clock=lambda: hub.now,
                    sleep=ci.sleep, root=tmp_path / "locks", holder="test line", suite=suite)


def passes(store):
    return store.query("SELECT COUNT(*) FROM events WHERE kind = 'step.started' AND step = 'test_run'").rows[0][0]


def test_four_green_members_merge_after_one_full_pass_and_main_is_the_tested_tree(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite)
    order = [prs[n] for n in (10, 11, 12, 13)]
    assert passes(store) == 1
    assert [s.pr for s in done.served] == order and {s.result for s in done.served} == {"merged"}
    assert place.tested == [(hub.branches["main"][-1], "passed")]  # main's sha is the tested tree's commit
    assert hub.advances == [hub.branches["main"][-1]]
    assert all(hub.pull(REPO, pr).state == "merged" for pr in order) and done.reason == "empty"
    assert ci.merged == order  # in line order


@pytest.mark.parametrize("red_ticket", [10, 11, 12, 13])
def test_a_batch_with_one_red_member_merges_the_other_three_in_at_most_five_passes(tmp_path, red_ticket):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path, red={prs[red_ticket]})
    done = line(world, tmp_path, suite)
    assert 1 < passes(store) <= 5
    merged = [prs[n] for n in (10, 11, 12, 13) if n != red_ticket]
    assert ci.merged == merged and hub.pull(REPO, prs[red_ticket]).state == "open"
    assert sorted((s.pr, s.result) for s in done.served) == sorted([(pr, "merged") for pr in merged]
                                                                   + [(prs[red_ticket], "left")])
    # every landing is a tree a pass tested green, and main ends on the last of them
    green = {tip for tip, outcome in place.tested if outcome == "passed"}
    assert hub.advances and set(hub.advances) <= green and hub.branches["main"][-1] == hub.advances[-1]
    issue = hub.issues[(REPO, red_ticket)]
    ticket = tickets.parse(issue.body, labels=issue.labels)
    assert ticket.ready and failing_id(prs[red_ticket]) in hub.comments(REPO, red_ticket)[-1].body
    counted = store.query("SELECT detail FROM events WHERE kind = 'attempt.counted' AND ticket = ?",
                          (red_ticket,)).rows
    assert [json.loads(row[0])["counted"] for row in counted] == [True]
    assert marks.ready_since(marks.of_layer(hub.pull_comments(REPO, prs[red_ticket]), REPO.split("/")[0])) is None


def test_a_member_that_cannot_be_combined_is_served_alone_not_dropped(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    hub.clashes.add(frozenset((prs[11], prs[12])))
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite)
    assert {s.result for s in done.served} == {"merged"} and len(done.served) == 4
    assert hub.members_of(hub.advances[0]) == sorted([prs[10], prs[11], prs[13]])
    assert hub.members_of(hub.advances[1]) == sorted(prs.values())  # on top of the first landing
    assert passes(store) == 2 and ci.merged[-1] == prs[12]


def test_main_moving_during_the_pass_rebuilds_the_batch_on_the_new_main(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    moved = []
    place.during.append(lambda: moved or moved.append(hub.land()))  # a push to main while the first pass runs
    done = line(world, tmp_path, suite)
    assert passes(store) == 2 and {s.result for s in done.served} == {"merged"}
    assert hub.branches["main"][-1] == hub.advances[-1] and moved[0] in hub.branches["main"]


def test_batch_size_one_is_the_line_as_it_was_and_runs_no_suite(tmp_path):
    world = line_world(tmp_path, tickets=(10, 11))
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite, batch_size=1)
    assert passes(store) == 0 and place.tested == []
    assert ci.merged == [prs[10], prs[11]] and [s.updates for s in done.served] == [0, 1]


def test_batch_size_is_a_setting_default_four_at_least_one():
    assert MergeSettings().batch_size == 4
    assert MergeSettings.from_settings({"merge": {"batch_size": 7}}, REPO).batch_size == 7
    assert MergeSettings.from_settings({"repos": {REPO: {"merge": {"batch_size": 2}}}}, REPO).batch_size == 2
    for bad in (0, -1, 1.5, True, "4"):
        with pytest.raises(SettingsError):
            MergeSettings.from_settings({"merge": {"batch_size": bad}}, REPO)
