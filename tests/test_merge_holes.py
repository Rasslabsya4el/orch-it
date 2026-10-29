"""The fourteen merge holes of #384 (MERGE-1..14; #400 acceptance row 1): each closed by the named test
below on fixture GitHub states through the one `gh` seam (`tests/fake_github.py`), with the outcome
the pipeline model gives it (`model.js` HOLES):

| hole | state | model outcome | test |
|---|---|---|---|
| MERGE-1 | a PR with no check run | test_run.passed: only required checks green on the head merge | test_merge_1 |
| MERGE-2 | green at an old base | pr.refused: a head behind main is updated and checked again | test_merge_2 |
| MERGE-3 | conflict after another lands | pr.refused: the ticket back to ready with the files | test_merge_3 |
| MERGE-4 | PR ends red / closed / stuck, no word | test_run.failed, pr.failed, pr.no_result: each moves the ticket | test_merge_4 |
| MERGE-5 | a cut run counted as an attempt | test_run.no_result: run once more, never an attempt | test_merge_5 |
| MERGE-6 | red on a test red on main too | test_run.failed: parked on the red-on-main ticket, not counted | test_merge_6 |
| MERGE-7 | a skip list in the tested tree | main_check.failed: no skip list, red main its own ticket | test_merge_7 |
| MERGE-8 | nothing runs on main after a merge | main_check.failed: main's run read after every merge | test_merge_8 |
| MERGE-9 | merged, the ticket stays open | close.no_result: the follow closes it, the sweep what it missed | test_merge_9 |
| MERGE-10 | `Closes #N`, no closing comment | close.failed: Refs never Closes, the close reads the rows first | test_merge_10 |
| MERGE-11 | the ticket closed or edited while the PR waits | pr.stopped: left mark | test_merge_11 |
| MERGE-12 | a green of an older head | test_run.stopped: every result read for the head sha | test_merge_12 |
| MERGE-13 | a person's PR comment unread | pr.stopped: left mark, the ticket to the brain | test_merge_13 |
| MERGE-14 | a park on a PR closed unmerged | park.failed: the event came the other way, to the brain | test_merge_14 |
"""

import json
import re
from pathlib import Path

import pytest
from fake_github import FAILED, NO_RESULT, PASSED, PENDING, REPO
from follow_world import ROWS_NO_AFTER, Git, world
from line_world import line_world

from orch_it import tickets
from orch_it.merge import marks, red_main
from orch_it.merge.follow import follow
from orch_it.merge.line import run_line
from orch_it.merge.main_check import check_main
from orch_it.merge.pr_open import PrError, open_pr
from orch_it.merge.reconcile import reconcile
from orch_it.merge.settings import MergeSettings
from orch_it.tickets.reader import read_parks
from required_names import LINUX_CHECK, WINDOWS_CHECK

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"


def follow_it(hub, store, pr, **settings):
    return follow(hub, store, REPO, pr, settings=MergeSettings(**settings), now=hub.now)


def line(hub, store, ci, tmp_path, **settings):
    return run_line(hub, store, REPO, "main", settings=MergeSettings(**settings), clock=lambda: hub.now,
                    sleep=ci.sleep, root=tmp_path / "locks")


def counted(store, ticket=10):
    return [json.loads(row[0])["counted"] for row in store.query(
        "SELECT detail FROM events WHERE kind = 'attempt.counted' AND ticket = :t", {"t": ticket}).rows]


def labels(hub, number=10):
    issue = hub.issues[(REPO, number)]
    return tickets.parse(issue.body, labels=issue.labels)


def test_merge_1(tmp_path):  # no check run: never green, never merged
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    ci.never.add(prs[10])
    hub.check_runs.pop(hub.head(prs[10]), None)
    ci.tick = lambda: None  # GitHub starts no check at all
    done = line(hub, store, ci, tmp_path, check_minutes=5)
    assert done.served[0].result == "bound" and hub.pull(REPO, prs[10]).state == "open"
    assert follow_it(hub, store, prs[10]).outcome == "waiting"


def test_merge_2(tmp_path):  # green at an old base: updated and checked again before the merge
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    hub.land()  # main moved
    ci.sleep(6 * 60)  # green at the old base: behind, so GitHub merges nothing
    done = line(hub, store, ci, tmp_path)
    assert (done.served[0].result, done.served[0].updates) == ("merged", 1)
    merged_head = hub.head(prs[10])
    assert [up for sha, up in ci.runs[prs[10]] if sha == merged_head] == [True]


def test_merge_3(tmp_path):  # conflict after another lands: the ticket back to ready with the files
    hub, store, prs, ci = line_world(tmp_path, tickets=(10, 11))
    hub.on_merge.append(lambda pr: setattr(hub.pulls[prs[11]], "conflict", ("checkout/cart.py",)))
    line(hub, store, ci, tmp_path)
    assert labels(hub, 11).ready and not labels(hub, 11).parked
    assert "checkout/cart.py" in hub.comments(REPO, 11)[-1].body
    assert counted(store, 11) == [False]  # a conflict is never an attempt


@pytest.mark.parametrize("end, outcome, back", [("red", "red", "ready"), ("closed", "closed_unmerged", "brain"),
                                                ("stuck", "timeout", "brain")])
def test_merge_4(tmp_path, end, outcome, back):  # red, closed or stuck: the ticket moves, with a comment
    hub, store, prs = world(tmp_path)
    pr = prs[10]
    if end == "red":
        hub.checks_on(hub.head(pr), FAILED, failing=["t::a"])
    elif end == "closed":
        hub.close_pull(pr)
    else:
        hub.checks_on(hub.head(pr), PENDING, started=hub.now)
        hub.later(hours=25)
    assert follow_it(hub, store, pr, check_stuck_minutes=60 * 48).outcome == outcome
    held = labels(hub)
    assert not held.parked and (held.ready if back == "ready" else "needs-triage" in held.labels)
    assert marks.LAYER_MARK in hub.comments(REPO, 10)[-1].body


def test_merge_5(tmp_path):  # a cut run: run once more, never an attempt
    hub, store, prs = world(tmp_path)
    run_id = hub.checks_on(hub.head(prs[10]), NO_RESULT)
    assert follow_it(hub, store, prs[10]).outcome == "rerun" and ("rerun", run_id) in hub.calls
    counted = store.query("SELECT json_extract(detail, '$.counted') FROM events WHERE kind = 'attempt.counted'").rows
    assert counted == [(0,)] and labels(hub).parked


def test_merge_6(tmp_path):  # red only on tests red on main: parked on that ticket, not counted
    hub, store, prs = world(tmp_path)
    title, body = red_main.build(["t::main"], sha="m0", run_id="7", pr=None)
    red = hub.create_issue(REPO, title, body, list(red_main.LABELS))
    hub.checks_on(hub.head(prs[10]), FAILED, failing=["t::main"])
    assert follow_it(hub, store, prs[10]).outcome == "red_on_main"
    assert labels(hub).park_lines[0].startswith(f"Parked: ticket_closed #{red} ")
    assert counted(store) == [False]


def test_merge_7(tmp_path):  # no skip list: what is red on main is read from main's run and its ticket alone
    hub, store, prs = world(tmp_path)
    hub.main_run(hub.land(), {LINUX_CHECK: FAILED, WINDOWS_CHECK: PASSED}, failing=["t::flaky"])
    done = check_main(hub, store, REPO, "main")
    assert done.outcome == "red" and len(done.opened) == 1
    # a pull request red on a test no red-on-main ticket lists is red on its own, whatever its tree says
    hub.checks_on(hub.head(prs[10]), FAILED, failing=["t::listed_in_the_branch_only"])
    assert follow_it(hub, store, prs[10]).outcome == "red" and counted(store) == [True]


def test_merge_8(tmp_path):  # main's run is read after every merge
    text = (WORKFLOWS / "main-check.yml").read_text("utf-8")
    trigger = text.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    assert trigger == "  workflow_run:\n    workflows: [tests]\n    types: [completed]"  # every ended run, no filter
    condition = re.search(r"    if: >-\n((?:      .*\n)+)", text).group(1).split()
    assert condition == ["github.event.workflow_run.head_branch", "==", "github.event.repository.default_branch", "&&",
                         "github.event.workflow_run.event", "!=", "'pull_request'"]
    tests = (WORKFLOWS / "tests.yml").read_text("utf-8")
    assert "push:\n    branches: [main]" in tests  # part 0's run on every push to main, read, not changed
    hub, store, _ = world(tmp_path)
    sha = hub.land(number=57)
    hub.main_run(sha, {LINUX_CHECK: FAILED, WINDOWS_CHECK: PASSED}, failing=["t::a"])
    [number] = check_main(hub, store, REPO, "main").opened
    assert "#57" in hub.issues[(REPO, number)].body


def test_merge_9(tmp_path):  # merged, its follow never ran: the sweep closes the ticket with its rows
    hub, store, prs = world(tmp_path)
    hub.checks_on(hub.head(prs[10]), PASSED)
    hub.merge(REPO, prs[10], hub.head(prs[10]), "merge")
    reconcile(hub, store, REPO, "main", settings=MergeSettings(), now=hub.now)
    assert not hub.issues[(REPO, 10)].open and "| 1 | pass |" in hub.comments(REPO, 10)[-1].body


def test_merge_10(tmp_path):  # Refs never Closes; the close reads the acceptance rows first
    hub, store, prs = world(tmp_path)
    hub.add(11, hub.issues[(REPO, 10)].body.split("\n\n", 1)[1], ["ready-for-agent"])
    hub.branches["ticket-11"] = [*hub.branches["main"], "t11"]

    class Closing(Git):
        def __call__(self, args):
            return "Closes #11\x00" if args[0] == "log" else super().__call__(args)

    with pytest.raises(PrError) as caught:
        open_pr(hub, store, REPO, 11, acceptance=ROWS_NO_AFTER, branch="ticket-11", base="main", git=Closing(hub),
                settings=MergeSettings(), now=hub.now)
    assert caught.value.code == "closing_keyword"
    with pytest.raises(PrError) as caught:  # nor in the text pr open puts in the pull request's body
        open_pr(hub, store, REPO, 11, acceptance=ROWS_NO_AFTER, branch="ticket-11", base="main", git=Git(hub),
                settings=MergeSettings(), now=hub.now, notes="this closes #11")
    assert caught.value.code == "closing_keyword"
    body = hub.pull(REPO, prs[10]).body
    assert body.startswith("Refs #10\n") and marks.closing_keywords(body) == []
    hub.notes[(REPO, 10)].clear()  # no acceptance comment left on the ticket
    hub.checks_on(hub.head(prs[10]), PASSED)
    hub.merge(REPO, prs[10], hub.head(prs[10]), "merge")
    assert follow_it(hub, store, prs[10]).outcome == "acceptance_missing" and hub.issues[(REPO, 10)].open


@pytest.mark.parametrize("change, outcome", [("closed", "withdrawn"), ("edited", "edited")])
def test_merge_11(tmp_path, change, outcome):  # the ticket closed or edited while the PR waits: left mark
    hub, store, prs = world(tmp_path)
    if change == "closed":
        hub.close(10, "not_planned")
    else:
        issue = hub.issues[(REPO, 10)]
        hub.add(10, issue.body.replace("10115", "10117"), issue.labels)
    assert follow_it(hub, store, prs[10]).outcome == outcome
    assert marks.ready_since(hub.comments(REPO, prs[10])) is None


def test_merge_12(tmp_path):  # a green of an older head is not the new head's, and nothing merges on it
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    ci.tick = lambda: None  # GitHub runs nothing more
    old = hub.head(prs[10])
    hub.checks_on(old, PASSED)
    hub.push(prs[10])
    assert follow_it(hub, store, prs[10]).outcome == "waiting"
    line(hub, store, ci, tmp_path, check_minutes=5)
    assert [c for c in hub.calls if c[0] == "merge"] == [] and hub.pull(REPO, prs[10]).state == "open"


def test_merge_13(tmp_path):  # a person's PR comment: left mark, the ticket to the brain
    hub, store, prs = world(tmp_path)
    hub.later(minutes=1)
    hub.note(prs[10], "Hold this one.", author="a-reviewer")
    assert follow_it(hub, store, prs[10]).outcome == "commented"
    assert marks.ready_since(hub.comments(REPO, prs[10])) is None and "needs-triage" in labels(hub).labels


def test_merge_14(tmp_path):  # a park on a PR closed unmerged: the event the other way, to the brain
    hub, store, prs = world(tmp_path)
    hub.close_pull(prs[10])
    reports = read_parks(hub, store, REPO, now=hub.now, follow=lambda pr: follow_it(hub, store, pr).outcome)
    assert [(r.number, r.outcome, r.detail) for r in reports] == [(10, "followed", "closed_unmerged")]
    assert "needs-triage" in labels(hub).labels and not labels(hub).parked
