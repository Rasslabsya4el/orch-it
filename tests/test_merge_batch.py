"""The merge line in batches (#414 item 5, row 3, on the fixture world): up to `batch_size` ready members are combined
on one branch from main, the required suite runs once on that tree through the place rule (`run_tests`, whose
`test_run` starts are the passes the store counts), green: main becomes exactly the tested tree and every member ends
merged; red: the batch halves down to the one red member, which goes back to its ticket while the others merge."""

import contextlib
import json

import pytest
from fake_github import FAILED, PASSED, PENDING, REPO, REQUIRED
from line_world import line_world
from required_names import LINUX_CHECK, LINUX_OS, WINDOWS_CHECK
from scan_setup import INVENTED

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


MAIN_RED = "tests.test_cart::test_total_on_main"  # a test that fails on main itself


def case(test_id, failed):
    classname, name = test_id.split("::")
    inside = "<failure message='x'/>" if failed else ""
    return f"<testcase classname='{classname}' name='{name}'>{inside}</testcase>"


class FixturePlace:
    """A place with no process: the tree it is given names the batch commit to test (`tip.txt`); the suite is red,
    naming `failing_id(pr)`, for each member of that commit in `red`. Each pass takes `PASS_MINUTES` of the hub's
    clock. Every tree also fails the ids of `main_red` (main itself is red on them). A run given ids runs only those.
    The pass leaves each required check name in `posted` on the tree's sha (as whichever place ran it would), in the
    state `PASSED`, or `PENDING` for a name in `pending` (CI turns it green after its minutes), or `FAILED` for a name in
    `red_names` when the tree holds one of the pull requests it maps to."""

    id, os = "own", LINUX_OS

    def __init__(self, hub, red, main_red=()):
        self.hub, self.red, self.main_red = hub, red, list(main_red)
        self.posted = list(REQUIRED)
        self.pending = set()
        self.red_names = {}  # check name -> the pull requests whose presence in a tree makes it red
        self.unposted_for = {}  # check name -> the pull requests whose presence in a tree leaves it unposted
        self.tested = []  # (tip, outcome) per pass, in order
        self.id_runs = []  # (tip, the ids asked) per pass that was given ids
        self.during = []  # callables run while a pass runs (the world moves under it)

    def run(self, request, scratch):
        tip = (request.work_tree / "tip.txt").read_text("utf-8")
        red = [failing_id(pr) for pr in self.hub.members_of(tip) if pr in self.red] + self.main_red
        if request.ids is None:
            cases = case("tests.test_cart::test_ok", False) + "".join(case(i, True) for i in red)
            failing = red
        else:
            self.id_runs.append((tip, list(request.ids)))
            failing = [i for i in request.ids if i in red]
            cases = "".join(case(i, i in failing) for i in request.ids)
        if request.ids is None:
            held = set(self.hub.members_of(tip))
            for name in self.posted:
                if held & self.unposted_for.get(name, set()):
                    continue
                state = FAILED if held & self.red_names.get(name, set()) else PENDING if name in self.pending else PASSED
                self.hub.checks_on(tip, state, names=(name,))
        report = scratch / "junit.xml"
        report.write_text(f"<testsuites><testsuite name='pytest'>{cases}</testsuite></testsuites>", "utf-8")
        self.tested.append((tip, "failed" if failing else "passed"))
        self.hub.later(minutes=PASS_MINUTES)
        for hook in self.during:
            hook()
        return PlaceEnd(report, False, False, 1 if failing else 0, 0.0, PASS_MINUTES * 60.0)


def make_suite(world, tmp_path, red=(), main_red=()):
    hub, store, _, ci = world
    place = FixturePlace(hub, set(red), main_red)

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


def close_on_first_batch(hub, number):
    """A person closes pull request `number` the moment the first batch branch is made."""

    start, closed = hub.start_batch, []

    def started(repo, base, branch):
        if not closed:
            closed.append(hub.close_pull(repo, number))
        return start(repo, base, branch)

    hub.start_batch = started


def turns_of(done):
    counted = {}
    for served in done.served:
        counted.setdefault(served.pr, []).append(served.result)
    return counted


def test_a_member_gone_from_the_batch_is_served_once_when_the_batch_is_rebuilt(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    close_on_first_batch(hub, prs[10])
    place.during.append(lambda: hub.land())  # main moves under the first pass only (the second finds it quiet)
    place.during.append(lambda: place.during.clear())
    done = line(world, tmp_path, suite)
    assert passes(store) == 2
    assert turns_of(done) == {prs[10]: ["left"], prs[11]: ["merged"], prs[12]: ["merged"], prs[13]: ["merged"]}


def test_a_member_gone_from_the_batch_is_served_once_when_main_keeps_moving_and_the_batch_starves(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    close_on_first_batch(hub, prs[10])
    place.during.append(lambda: hub.land())  # main moves under every pass
    done = line(world, tmp_path, suite, max_updates=1)
    assert passes(store) == 2
    assert turns_of(done) == {prs[10]: ["left"], prs[11]: ["starved"], prs[12]: ["starved"], prs[13]: ["starved"]}
    assert done.reason == "next_run"


def test_a_landing_github_marks_merged_within_a_minute_ends_every_member_merged(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    hub.merge_lag_seconds = 20
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite)
    assert passes(store) == 1 and [s.result for s in done.served] == ["merged"] * 4
    assert ci.merged == [prs[n] for n in (10, 11, 12, 13)] and done.reason == "empty"


def test_a_landing_github_does_not_mark_merged_in_a_minute_is_reported_pending_and_not_served_again(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    hub.merge_lag_seconds = 3600
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite)
    assert passes(store) == 1 and done.merged == 0 and done.reason == "next_run"
    assert [(s.result, s.detail) for s in done.served] == [("bound", "landed_pending")] * 4
    assert [s.pr for s in done.served] == [prs[n] for n in (10, 11, 12, 13)]
    hub.later(seconds=3600)
    again = line(world, tmp_path, suite)
    assert again.served == () and again.reason == "empty" and passes(store) == 1
    assert all(hub.pull(REPO, pr).state == "merged" for pr in prs.values())


def test_a_base_whose_protection_refuses_the_landing_is_noted_and_the_line_goes_on_one_by_one(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    hub.advance_refusal = "Changes must be made through a pull request."
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite)
    assert done.notes == ("batch landing refused: Changes must be made through a pull request.",)
    assert passes(store) == 1 and hub.advances == []  # one pass, then no second batch is tried
    assert [(s.pr, s.result) for s in done.served] == [(prs[n], "merged") for n in (10, 11, 12, 13)]
    assert ci.merged == [prs[n] for n in (10, 11, 12, 13)] and done.reason == "empty"


def leave_batch_branches(hub, age_minutes):
    """Batch branches a line that died left: names by the second they were made, one that is not a time."""

    now = int(hub.now.timestamp())
    names = {"old": f"orch-batch/{now - age_minutes * 60}-1", "young": f"orch-batch/{now - 60}-1",
             "unparsable": "orch-batch/made-by-hand"}
    for name in names.values():
        hub.branches[name] = list(hub.branches["main"])
    return names


def test_a_line_that_batches_deletes_the_old_and_unparsable_batch_branches_and_keeps_a_young_one(tmp_path):
    world = line_world(tmp_path, tickets=(10,))
    hub, store, prs, ci = world
    names = leave_batch_branches(hub, MergeSettings().lock_stale_minutes + 1)
    suite, place = make_suite(world, tmp_path)
    line(world, tmp_path, suite)
    assert hub.batch_branches(REPO) == [names["young"]]


def test_batch_size_one_cleans_no_batch_branch(tmp_path):
    world = line_world(tmp_path, tickets=(10,))
    hub, store, prs, ci = world
    names = leave_batch_branches(hub, MergeSettings().lock_stale_minutes + 1)
    suite, place = make_suite(world, tmp_path)
    line(world, tmp_path, suite, batch_size=1)
    assert hub.batch_branches(REPO) == sorted(names.values())


def test_stale_is_older_than_the_minutes_by_the_time_in_the_name_or_a_name_with_none():
    from datetime import UTC, datetime

    from orch_it.merge.batch import branch_name, stale

    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    made = datetime(2026, 11, 2, 10, tzinfo=UTC)
    assert branch_name(made, 3) == f"orch-batch/{int(made.timestamp())}-3"
    assert stale(branch_name(made, 3), now, 60) and not stale(branch_name(made, 3), now, 180)
    assert stale("orch-batch/by-hand", now, 180) and stale("orch-batch/", now, 180)


def open_red_on_main(hub, ids):
    from orch_it.merge import red_main

    title, body = red_main.build(list(ids), sha="m0", run_id="7", pr=None)
    return hub.create_issue(REPO, title, body, list(red_main.LABELS))


def test_a_batch_red_only_on_what_main_is_red_on_stops_with_one_pass_and_one_check_of_those_ids(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    open_red_on_main(hub, [MAIN_RED])
    suite, place = make_suite(world, tmp_path, main_red=[MAIN_RED])
    base = hub.branches["main"][-1]
    said = {ticket: len(hub.comments(REPO, ticket)) for ticket in prs}
    done = line(world, tmp_path, suite)
    assert passes(store) == 2 and place.id_runs == [(base, [MAIN_RED])]  # the batch, then main's own result on its ids
    assert done.merged == 0 and done.left == 0 and hub.advances == [] and done.reason == "next_run"
    assert [(s.pr, s.result, s.detail) for s in done.served] == [(prs[n], "bound", "main_red") for n in (10, 11, 12, 13)]
    assert done.notes == (f"main is red on {MAIN_RED}",)
    for ticket in (10, 11, 12, 13):  # nothing is sent back: no comment on a ticket, no pull request left
        assert len(hub.comments(REPO, ticket)) == said[ticket]
        assert marks.ready_since(marks.of_layer(hub.pull_comments(REPO, prs[ticket]), REPO.split("/")[0])) is not None


def test_a_batch_with_a_failing_id_main_is_not_red_on_splits_as_before_in_five_passes(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    open_red_on_main(hub, [MAIN_RED])  # a ticket for another test: it covers nothing of this batch
    suite, place = make_suite(world, tmp_path, red={prs[11]})
    done = line(world, tmp_path, suite)
    assert passes(store) == 5 and place.id_runs == [] and done.notes == ()
    assert ci.merged == [prs[10], prs[12], prs[13]] and done.left == 1


def test_a_red_main_ticket_that_main_no_longer_fails_does_not_stop_the_split(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    open_red_on_main(hub, [failing_id(prs[11])])  # a stale ticket: it covers the batch's red id, main passes it
    suite, place = make_suite(world, tmp_path, red={prs[11]})
    done = line(world, tmp_path, suite)
    assert [ids for _, ids in place.id_runs] == [[failing_id(prs[11])]] * 2  # the whole batch, then its half
    assert ci.merged == [prs[10], prs[12], prs[13]] and done.left == 1 and passes(store) == 5 + 2
    assert done.notes == ()


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


def git_in(folder, *args):
    from orch_it.merge import git as merge_git

    identity = ["-c", "user.name=" + INVENTED[("git", "config", "user.name")],
                "-c", "user.email=" + INVENTED[("git", "config", "user.email")]]
    done = merge_git.run(["-C", str(folder), *identity, *args])
    assert done.code == 0, done.err
    return done.out.strip()


def origin_with_a_batch(tmp_path):
    """A bare origin holding main and a `batch` branch, and a clone of it that knows only main: (clone, batch sha)."""

    origin, maker, line_tree = tmp_path / "origin.git", tmp_path / "maker", tmp_path / "line"
    git_in(tmp_path, "init", "--quiet", "--bare", "--initial-branch=main", str(origin))
    git_in(tmp_path, "clone", "--quiet", str(origin), str(maker))
    (maker / "base.txt").write_text("base", "utf-8")
    git_in(maker, "add", "-A")
    git_in(maker, "commit", "--quiet", "-m", "base")
    git_in(maker, "push", "--quiet", "origin", "HEAD:main")
    git_in(tmp_path, "clone", "--quiet", str(origin), str(line_tree))  # the line's work tree knows only the base
    heads = {}
    for name in ("first", "second"):
        git_in(maker, "checkout", "--quiet", "-b", name, "origin/main")
        (maker / f"{name}.txt").write_text(name, "utf-8")
        git_in(maker, "add", "-A")
        git_in(maker, "commit", "--quiet", "-m", name)
        heads[name] = git_in(maker, "rev-parse", "HEAD")
    git_in(maker, "checkout", "--quiet", "-b", "batch", heads["first"])
    git_in(maker, "merge", "--quiet", "--no-edit", "--no-ff", heads["second"])
    batch = git_in(maker, "rev-parse", "HEAD")
    git_in(maker, "push", "--quiet", "origin", "batch")
    return line_tree, batch


def test_git_checkout_makes_a_work_tree_of_a_commit_only_the_origin_has_and_removes_it(tmp_path):
    from pathlib import Path

    from orch_it.merge.batch import git_checkout
    from orch_it.merge.git import GitError

    line_tree, batch = origin_with_a_batch(tmp_path)
    checkout = git_checkout(line_tree)
    with checkout(batch) as tree:
        assert {p.name for p in Path(tree).iterdir() if p.name != ".git"} == {"base.txt", "first.txt", "second.txt"}
        assert git_in(tree, "rev-parse", "HEAD") == batch
        kept = Path(tree)
    assert not kept.exists()
    assert len(git_in(line_tree, "worktree", "list").splitlines()) == 1
    with pytest.raises(GitError), checkout("0" * 40):
        pass


def test_a_checkout_prunes_the_work_tree_a_dead_line_left_behind(tmp_path):
    import shutil

    from orch_it.merge.batch import git_checkout

    line_tree, batch = origin_with_a_batch(tmp_path)
    left = tmp_path / "left-by-a-dead-line"
    git_in(line_tree, "worktree", "add", "--quiet", "--detach", str(left))
    shutil.rmtree(left)  # the folder is gone, git still lists the work tree
    assert len(git_in(line_tree, "worktree", "list").splitlines()) == 2
    with git_checkout(line_tree)(batch):
        pass
    assert len(git_in(line_tree, "worktree", "list").splitlines()) == 1


# the required checks gate a landing (#414 brain answer 2): the pass replaces no required check

def unprotected(world):
    """A base that protects nothing (rule 7): only the line holds the gate."""

    world[0].required_by_base = {}


def main_untouched(hub):
    return hub.advances == [] and hub.branches["main"] == ["m0"]


@pytest.mark.parametrize("protected", [True, False])
def test_a_required_name_nobody_posted_on_the_tested_sha_merges_nothing_and_main_is_untouched(tmp_path, protected):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    if not protected:
        unprotected(world)
    suite, place = make_suite(world, tmp_path)
    place.posted = [LINUX_CHECK]  # a pass that ran only the suite: the other name stays unposted
    done = line(world, tmp_path, suite)
    assert main_untouched(hub) and ci.merged == [] and passes(store) == 1
    assert all(call[0] != "advance_base" for call in hub.calls)  # the line does not even ask for the update
    assert [(s.pr, s.result, s.detail) for s in done.served] == [(prs[n], "bound", "checks_missing")
                                                                 for n in (10, 11, 12, 13)]
    assert done.reason == "next_run" and len(done.notes) == 1 and WINDOWS_CHECK in done.notes[0]
    assert LINUX_CHECK not in done.notes[0]
    assert all(hub.pull(REPO, pr).state == "open" for pr in prs.values())


def test_the_wait_for_the_names_ends_on_the_clock_at_check_minutes_and_names_the_missing_check(tmp_path):
    world = line_world(tmp_path, tickets=(10,))
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    place.posted = []
    pass_ended = []
    place.during.append(lambda: pass_ended.append(hub.now))
    done = line(world, tmp_path, suite, check_minutes=2, poll_seconds=30)
    assert main_untouched(hub) and done.merged == 0 and done.reason == "next_run"
    waited = (hub.now - pass_ended[0]).total_seconds()
    assert 120 <= waited <= 120 + 30  # the bound on the clock, not a count of polls
    assert LINUX_CHECK in done.notes[0] and WINDOWS_CHECK in done.notes[0]


def test_names_still_pending_when_the_pass_ends_are_waited_for_and_main_moves_to_that_sha_once_all_are_green(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    place.pending = {LINUX_CHECK, WINDOWS_CHECK}  # posted pending: CI ends them after its minutes
    before = hub.now
    done = line(world, tmp_path, suite)
    tip = hub.branches["main"][-1]
    assert passes(store) == 1 and hub.advances == [tip] and ci.merged == [prs[n] for n in (10, 11, 12, 13)]
    assert {name: c.state for name, c in hub.check_runs[tip].items()} == {name: PASSED for name in REQUIRED}
    assert (hub.now - before).total_seconds() > PASS_MINUTES * 60 and done.reason == "empty"  # it polled past the pass


@pytest.mark.parametrize("protected", [True, False])
def test_a_required_name_red_on_the_sha_of_a_lone_member_merges_nothing_and_sends_that_member_back(tmp_path, protected):
    world = line_world(tmp_path, tickets=(10,))
    hub, store, prs, ci = world
    if not protected:
        unprotected(world)
    suite, place = make_suite(world, tmp_path)
    place.red_names = {WINDOWS_CHECK: {prs[10]}}
    done = line(world, tmp_path, suite)
    assert main_untouched(hub) and ci.merged == [] and passes(store) == 1
    assert [(s.pr, s.result) for s in done.served] == [(prs[10], "left")]
    body = hub.comments(REPO, 10)[-1].body
    assert WINDOWS_CHECK in body
    issue = hub.issues[(REPO, 10)]
    assert tickets.parse(issue.body, labels=issue.labels).ready


@pytest.mark.parametrize("protected", [True, False])
def test_a_name_red_for_one_member_of_four_splits_the_batch_and_only_green_shas_land(tmp_path, protected):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    if not protected:
        unprotected(world)
    suite, place = make_suite(world, tmp_path)
    place.red_names = {WINDOWS_CHECK: {prs[12]}}
    done = line(world, tmp_path, suite)
    assert 1 < passes(store) <= 5
    assert ci.merged == [prs[10], prs[11], prs[13]] and hub.pull(REPO, prs[12]).state == "open"
    assert sorted((s.pr, s.result) for s in done.served) == sorted([(prs[n], "merged") for n in (10, 11, 13)]
                                                                   + [(prs[12], "left")])
    for tip in hub.advances:  # every landing had every required name green on its exact sha
        assert {name: c.state for name, c in hub.check_runs[tip].items()} == {name: PASSED for name in REQUIRED}


def test_the_bases_own_protection_still_decides_a_name_the_line_does_not_require(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    hub.required_by_base = {"main": {*REQUIRED, "scan-of-another-kind"}}  # protection wants one more name
    suite, place = make_suite(world, tmp_path)
    done = line(world, tmp_path, suite)
    assert len(done.notes) == 1 and done.notes[0].startswith("batch landing refused:")
    assert [c for c in hub.calls if c[0] == "advance_base"] and hub.advances == []  # the line asked, GitHub refused


def test_a_hold_during_a_split_puts_every_member_not_landed_and_not_sent_back_off_to_the_next_run(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    suite, place = make_suite(world, tmp_path)
    place.red_names = {WINDOWS_CHECK: {prs[13]}}  # the whole batch is red on a name: it splits
    place.unposted_for = {LINUX_CHECK: {prs[12]}}  # the half holding 12 never gets the other name: the run holds
    done = line(world, tmp_path, suite)
    results = {s.pr: (s.result, s.detail) for s in done.served}
    assert results == {prs[10]: ("merged", ""), prs[11]: ("merged", ""), prs[12]: ("bound", "checks_missing"),
                       prs[13]: ("bound", "checks_missing")}
    assert done.reason == "next_run" and ci.merged == [prs[10], prs[11]]
    assert hub.pull(REPO, prs[13]).state == "open" and hub.issues[(REPO, 13)].open


def test_a_hold_also_puts_off_the_member_that_did_not_combine_with_the_batch(tmp_path):
    world = line_world(tmp_path)
    hub, store, prs, ci = world
    hub.clashes.add(frozenset((prs[11], prs[12])))  # 12 does not combine: it waits `apart`
    suite, place = make_suite(world, tmp_path)
    place.posted = [LINUX_CHECK]
    done = line(world, tmp_path, suite)
    assert main_untouched(hub)
    assert {s.pr: (s.result, s.detail) for s in done.served} == {prs[n]: ("bound", "checks_missing")
                                                                 for n in (10, 11, 12, 13)}
