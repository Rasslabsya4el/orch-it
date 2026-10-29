"""The merge line (#400 decision 2; rows 2, 3 and 7 on fixture GitHub): one line per repo under its
line lock, the oldest ready first; its head brought up to date, its checks waited for within their
bound, merged at its head sha by the line itself (nothing in GitHub merges: #414), so nothing lands out of
order and no green pull request starves."""

import json
from datetime import timedelta

import pytest
from fake_github import REPO
from follow_world import NO_AFTER_MERGE, ROWS_NO_AFTER, TITLE, Git
from line_world import line_world

from orch_it import tickets
from orch_it.merge import lock, marks
from orch_it.merge.line import run_line
from orch_it.merge.pr_open import open_pr
from orch_it.merge.settings import MergeSettings
from required_names import LINUX_CHECK, WINDOWS_CHECK


def line(hub, store, ci, tmp_path, **settings):
    return run_line(hub, store, REPO, "main", settings=MergeSettings(**settings), clock=lambda: hub.now,
                    sleep=ci.sleep, root=tmp_path / "locks", holder="test line")


def in_line(hub, pr):
    return marks.ready_since(marks.of_layer(hub.pull_comments(REPO, pr), REPO.split("/")[0])) is not None


def test_four_pull_requests_made_ready_at_once_merge_in_order_each_after_one_up_to_date_run(tmp_path):
    hub, store, prs, ci = line_world(tmp_path)
    done = line(hub, store, ci, tmp_path)
    order = [prs[n] for n in (10, 11, 12, 13)]
    assert ci.merged == order  # the line merges the head, never one behind it
    assert [s.pr for s in done.served] == order and {s.result for s in done.served} == {"merged"}
    for pr in order:  # the head each one merged at had exactly one run, on a head up to date with main
        merged_head = hub.head(pr)
        assert [up_to_date for sha, up_to_date in ci.runs[pr] if sha == merged_head] == [True], (pr, ci.runs[pr])
    # one update each behind the first: one run of the line's per pull request, never a run thrown away twice
    assert [s.updates for s in done.served] == [0, 1, 1, 1]
    assert [len(ci.runs[pr]) for pr in order] == [1, 2, 2, 2]  # its run at opening, then the line's one
    waited = [s.waited_minutes for s in done.served]
    assert waited == sorted(waited) and waited[-1] <= 4 * 6  # one check pass per pull request ahead of it
    assert done.reason == "empty"


def test_a_pull_request_that_turns_conflicting_in_the_line_returns_its_ticket_and_holds_nobody(tmp_path):
    hub, store, prs, ci = line_world(tmp_path)
    hub.on_merge.append(lambda pr: pr == prs[11] and setattr(hub.pulls[prs[12]], "conflict", ("checkout/cart.py",)))
    done = line(hub, store, ci, tmp_path)
    assert ci.merged == [prs[10], prs[11], prs[13]]
    assert [(s.pr, s.result) for s in done.served] == [(prs[10], "merged"), (prs[11], "merged"),
                                                       (prs[12], "left"), (prs[13], "merged")]
    issue = hub.issues[(REPO, 12)]
    ticket = tickets.parse(issue.body, labels=issue.labels)
    assert ticket.ready and not ticket.parked and "checkout/cart.py" in hub.comments(REPO, 12)[-1].body
    counted = store.query("SELECT detail FROM events WHERE kind = 'attempt.counted' AND ticket = 12").rows
    assert [json.loads(row[0])["counted"] for row in counted] == [False]


def test_a_red_head_leaves_the_line_and_the_next_goes(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10, 11))
    ci.red[prs[10]] = ["tests/test_cart.py::test_quote"]
    done = line(hub, store, ci, tmp_path)
    assert ci.merged == [prs[11]]
    assert [(s.pr, s.result, s.detail) for s in done.served][0] == (prs[10], "left", "red")
    assert tickets.parse(hub.issues[(REPO, 10)].body, labels=hub.issues[(REPO, 10)].labels).ready


def test_a_head_whose_checks_pass_their_bound_waits_for_the_next_run_and_the_next_goes(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10, 11))
    ci.never.add(prs[10])
    done = line(hub, store, ci, tmp_path, check_minutes=10, check_stuck_minutes=120)
    assert [(s.pr, s.result) for s in done.served] == [(prs[10], "bound"), (prs[11], "merged")]
    assert hub.pull(REPO, prs[10]).state == "open" and in_line(hub, prs[10])


def test_main_moving_under_the_head_each_time_leaves_it_to_the_next_run(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]

    def push_to_main_before_each_run_ends():  # someone else pushes to main a minute before the head turns green
        check = hub.check_runs.get(hub.head(pr), {}).get(LINUX_CHECK)
        if hub.pulls[pr].state == "open" and check and check.state == "pending" \
                and hub.now - check.started_at >= timedelta(minutes=ci.minutes - 1):
            hub.land()

    ci.on_tick.append(push_to_main_before_each_run_ends)
    done = line(hub, store, ci, tmp_path, max_updates=2)
    assert [(s.pr, s.result, s.updates) for s in done.served] == [(pr, "starved", 2)]
    assert hub.pull(REPO, pr).state == "open" and in_line(hub, pr)  # stays in the line


def test_a_pull_request_made_ready_while_the_head_waits_waits_its_turn_and_never_merges_first(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    head = prs[10]
    ci.slow[head] = 9  # the head's checks are slow; the newcomer's would end first
    fresh = {}

    def ready_a_newcomer_at_minute_two():
        if not fresh and hub.now - hub.pull(REPO, head).created_at >= timedelta(minutes=2):
            hub.add(11, NO_AFTER_MERGE, ["ready-for-agent"], title=TITLE)
            hub.branches["ticket-11"] = [*hub.branches["main"], hub.sha("t11-")]
            fresh[11] = open_pr(hub, store, REPO, 11, acceptance=ROWS_NO_AFTER, branch="ticket-11", base="main",
                                git=Git(hub), settings=MergeSettings(), now=hub.now).pr

    ci.on_tick.append(ready_a_newcomer_at_minute_two)
    done = line(hub, store, ci, tmp_path)
    assert ci.merged == [head, fresh[11]]  # the newcomer's checks end first, and it still lands second
    assert [s.result for s in done.served] == ["merged", "merged"]


def test_the_line_bound_ends_the_run_and_leaves_every_unmerged_pull_request_in_the_line(tmp_path):
    hub, store, prs, ci = line_world(tmp_path)
    done = line(hub, store, ci, tmp_path, line_minutes=8, lock_stale_minutes=60)
    assert done.reason == "bound" and len(ci.merged) < 4
    for ticket in (10, 11, 12, 13):
        pull = hub.pull(REPO, prs[ticket])
        assert pull.state == "merged" or in_line(hub, prs[ticket])


def test_an_empty_line_ends_at_once(tmp_path):
    hub, store, _, ci = line_world(tmp_path, tickets=())
    done = line(hub, store, ci, tmp_path)
    assert (done.served, done.reason) == ((), "empty")


def test_drafts_forks_and_pull_requests_out_of_the_line_are_not_served(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    draft = hub.open_pull(body="Refs #10", draft=True)
    fork = hub.open_pull(body="a stranger's")
    hub.pulls[fork].head_repo = "stranger/repo-y"
    for pr in (draft, fork):  # both carry a ready mark; neither is served
        hub.comment(REPO, pr, marks.ready_comment(None, hub.head(pr), hub.now))
    idle = hub.open_pull(body="no ready mark")
    done = line(hub, store, ci, tmp_path)
    assert [s.pr for s in done.served] == [prs[10]] and {draft, fork, idle}.isdisjoint(ci.merged)


def test_a_second_line_on_the_same_repo_waits_says_who_holds_it_and_gives_up(tmp_path):
    hub, store, _, ci = line_world(tmp_path, tickets=(10,))
    told = []
    with lock.hold(REPO, "merge line pid 1", settings=MergeSettings(), root=tmp_path / "locks", clock=lambda: hub.now):
        with pytest.raises(lock.LockError) as caught:
            run_line(hub, store, REPO, "main", settings=MergeSettings(lock_wait_minutes=1), clock=lambda: hub.now,
                     sleep=ci.sleep, root=tmp_path / "locks", holder="line B", on_wait=told.append)
    assert caught.value.code == "lock_busy" and [h.holder for h in told] == ["merge line pid 1"]
    rows = store.query("SELECT outcome, json_extract(detail, '$.reason') FROM events "
                       "WHERE kind = 'step.ended' AND step = 'line'").rows
    assert rows == [("refused", "lock_busy")]


def test_a_member_a_person_commented_on_while_it_waited_leaves_the_line_when_its_turn_comes(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10, 11))
    said = []

    def a_person_says_wait():  # while #10 is the head, a person asks to wait on #11
        if not said:
            said.append(1)
            hub.note(prs[11], "wait, the zones change", author="owner-x")

    ci.on_tick.append(a_person_says_wait)
    done = line(hub, store, ci, tmp_path)
    assert ci.merged == [prs[10]]
    assert not in_line(hub, prs[11])  # out of the line: the person's comment is read first
    assert "needs-triage" in hub.issues[(REPO, 11)].labels
    assert [(s.pr, s.result, s.detail) for s in done.served if s.pr == prs[11]] == [(prs[11], "left", "commented")]
    assert done.left == 1


def test_the_line_closes_the_ticket_of_the_head_it_merged(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    line(hub, store, ci, tmp_path)
    assert not hub.issues[(REPO, 10)].open


def test_a_run_that_breaks_ends_its_step_and_frees_the_lock(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10, 11, 12))
    real = hub.update_branch

    def broken(repo, number, head_sha):
        raise RuntimeError("the network went away")

    hub.land()  # every member behind: the head's update is the first write
    hub.update_branch = broken
    with pytest.raises(RuntimeError):
        line(hub, store, ci, tmp_path)
    hub.update_branch = real
    assert all(in_line(hub, prs[n]) for n in (10, 11, 12))
    assert store.query("SELECT outcome, json_extract(detail, '$.reason') FROM events "
                       "WHERE kind = 'step.ended' AND step = 'line'").rows == [("failed", "error")]
    assert lock.holder_of(REPO, root=tmp_path / "locks") is None


def test_a_run_that_leaves_members_for_the_next_one_says_so(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    ci.never.add(prs[10])
    done = line(hub, store, ci, tmp_path, check_minutes=10, check_stuck_minutes=120)
    assert done.reason == "next_run" and [s.result for s in done.served] == ["bound"]


def test_a_member_that_left_on_a_conflict_is_served_in_the_store_too(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10, 11))
    hub.pulls[prs[11]].conflict = ("checkout/cart.py",)
    line(hub, store, ci, tmp_path)
    served = store.query("SELECT json_extract(detail, '$.pr'), json_extract(detail, '$.result'), run FROM events "
                         "WHERE kind = 'line.served' ORDER BY at").rows
    assert [(pr, result) for pr, result, _ in served] == [(prs[11], "left"), (prs[10], "merged")]
    assert all(run.startswith(f"line:{REPO}@") for _, _, run in served)


def test_a_stale_line_lock_is_reported_and_the_line_does_not_run(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    dead = lock.hold(REPO, "a line that died", settings=MergeSettings(), root=tmp_path / "locks",
                     clock=lambda: hub.now - timedelta(hours=3))
    dead.__enter__()
    with pytest.raises(lock.LockError) as caught:
        line(hub, store, ci, tmp_path)
    assert caught.value.code == "lock_stale" and ci.merged == []
    rows = store.query("SELECT outcome, json_extract(detail, '$.reason') FROM events "
                       "WHERE kind = 'step.ended' AND step = 'line'").rows
    assert rows == [("refused", "lock_stale")]


def test_a_follow_that_raced_waits_a_poll_instead_of_spinning(tmp_path, monkeypatch):
    from orch_it.merge import line as line_module
    from orch_it.merge.follow import FollowReport

    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    ci.never.add(prs[10])
    monkeypatch.setattr(line_module, "follow", lambda *a, **k: FollowReport(prs[10], 10, "raced"))
    slept = []
    real = ci.sleep
    ci.sleep = lambda s: (slept.append(s), real(s))
    done = line(hub, store, ci, tmp_path, check_minutes=5)
    assert done.served[0].result == "bound" and 5 <= len(slept) <= 12


def test_the_line_writes_one_line_step_and_one_served_event_per_pull_request(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10, 11))
    line(hub, store, ci, tmp_path)
    steps = store.query("SELECT kind, outcome, json_extract(detail, '$.merged') FROM events WHERE step = 'line'").rows
    assert steps == [("step.started", None, None), ("step.ended", "passed", 2)]
    served = store.query("SELECT json_extract(detail, '$.pr'), json_extract(detail, '$.result') FROM events "
                         "WHERE kind = 'line.served' ORDER BY at").rows
    assert served == [(prs[10], "merged"), (prs[11], "merged")]
