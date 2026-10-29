"""The ticket follows its pull request (#400 decision 3; row 4 on fixture GitHub states): every end
of a pull request moves its ticket once, through #398's park and the ticket shape, and is written
to the store; check results are read for the head sha only."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from fake_github import FAILED, NO_RESULT, PASSED, PENDING, REPO
from follow_world import BODY, ROWS, ROWS_NO_AFTER, Git, world

from orch_it import tickets
from orch_it.merge import marks, red_main
from orch_it.merge.follow import follow
from orch_it.merge.pr_open import open_pr
from orch_it.merge.settings import MergeSettings
from required_names import LINUX_CHECK, WINDOWS_CHECK


def run(hub, store, pr, **settings):
    return follow(hub, store, REPO, pr, base="main", settings=MergeSettings(**settings), now=hub.now)


def ticket(hub, number=10):
    issue = hub.issues[(REPO, number)]
    return tickets.parse(issue.body, labels=issue.labels), issue


def last_comment(hub, number):
    return hub.comments(REPO, number)[-1].body


def events(store, *kinds):
    """(kind, step, outcome, reason, detail as a dict) of the events of `kinds`, in time order."""

    named = ", ".join(f"'{k}'" for k in kinds)
    rows = store.query(f"SELECT kind, step, outcome, json_extract(detail, '$.reason'), detail FROM events "
                       f"WHERE kind IN ({named}) ORDER BY at").rows
    return [(*row[:4], json.loads(row[4])) for row in rows]


def merged(hub, pr):
    hub.checks_on(hub.head(pr), PASSED)
    assert hub.merge(REPO, pr, hub.head(pr), "merge")


# merged


def test_merged_closes_the_ticket_with_its_acceptance_rows_and_the_pull_request(tmp_path):
    hub, store, prs = world(tmp_path)
    pr = prs[10]
    merged(hub, pr)
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("closed", 10)
    held, issue = ticket(hub)
    assert not issue.open and issue.state_reason == "completed" and not held.park_lines and not held.parked
    said = last_comment(hub, 10)
    assert f"#{pr}" in said and "| 1 | pass |" in said and "| 3 | pass |" in said
    closed = events(store, "ticket.closed")
    assert len(closed) == 1 and closed[0][4]["via"] == "close_command" and closed[0][4]["rows_passed"] == 3
    assert ("step.ended", "pr", "passed", "merged") in [row[:4] for row in events(store, "step.ended")]
    assert ("step.ended", "close", "passed", None) in [row[:4] for row in events(store, "step.ended")]


def test_merged_with_an_after_merge_row_returns_the_ticket_to_ready_for_that_row_only(tmp_path):
    hub, store, prs = world(tmp_path, body=BODY, rows=ROWS)
    merged(hub, prs[10])
    done = run(hub, store, prs[10])
    assert done.outcome == "after_merge"
    held, issue = ticket(hub)
    assert issue.open and not held.park_lines and not held.parked and held.ready
    said = last_comment(hub, 10)
    assert "row 4" in said and "| 1 |" not in said
    assert any(row[4]["returned_as"] == "due" for row in events(store, "ticket.unparked"))


@pytest.mark.parametrize("change, reason", [
    (lambda hub, n, pr: hub.notes[(REPO, n)].clear(), "acceptance_missing"),
    (lambda hub, n, pr: hub.note(n, "Wait, should 10115 not be free?", author="owner-x", at=hub.now + timedelta(minutes=1)),
     "unanswered"),
    (lambda hub, n, pr: hub.add(n, hub.issues[(REPO, n)].body.replace("10115", "10117"), hub.issues[(REPO, n)].labels),
     "ticket_changed"),
])
def test_merged_but_not_closable_goes_to_the_brain_and_stays_open(tmp_path, change, reason):
    hub, store, prs = world(tmp_path)
    change(hub, 10, prs[10])
    merged(hub, prs[10])
    done = run(hub, store, prs[10])
    assert done.outcome == reason
    held, issue = ticket(hub)
    assert issue.open and "needs-triage" in held.labels and not held.ready and not held.parked
    assert ("step.ended", "close", "failed", reason) in [row[:4] for row in events(store, "step.ended")]


def test_a_ticket_closed_by_hand_before_the_merge_is_only_recorded(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.close(10)
    writes = len(hub.writes)
    merged(hub, prs[10])
    done = run(hub, store, prs[10])
    assert done.outcome == "already_closed" and len(hub.writes) == writes
    assert ("step.ended", "close", "refused", "already_closed") in [row[:4] for row in events(store, "step.ended")]


def test_a_merged_pull_request_with_no_ticket_ends_its_step_only(tmp_path):
    hub, store, _ = world(tmp_path)
    pr = hub.open_pull(body="A hand change")
    merged(hub, pr)
    assert run(hub, store, pr).outcome == "merged_alone"


# merged: a part of a ticket (#434)


def open_part(hub, store, branch, part, number=10):
    hub.branches[branch] = [*hub.branches["main"], hub.sha(f"{branch}-")]
    return open_pr(hub, store, REPO, number, acceptance=ROWS_NO_AFTER, branch=branch, base="main", git=Git(hub),
                   settings=MergeSettings(), now=hub.now, part=part).pr


def test_a_merged_part_before_the_last_says_it_landed_and_leaves_the_ticket_open_and_unparked(tmp_path):
    hub, store, prs = world(tmp_path, part=(1, 2))
    merged(hub, prs[10])
    done = run(hub, store, prs[10])
    assert (done.outcome, done.ticket, done.detail) == ("part_landed", 10, "part 1 of 2")
    held, issue = ticket(hub)
    assert issue.open and issue.state_reason is None and not held.park_lines and not held.parked
    assert issue.labels == ("ready-for-agent",)
    said = last_comment(hub, 10)
    assert "part 1 of 2 landed" in said and f"#{prs[10]}" in said and hub.pulls[prs[10]].merge_sha[:12] in said
    assert marks.follows(hub.comments(REPO, 10))[-1].outcome == "part_landed"
    assert not events(store, "ticket.closed")
    ended = [row[:4] for row in events(store, "step.ended")]
    assert ("step.ended", "pr", "passed", "merged") in ended and ("step.ended", "close", "passed", None) in ended
    assert any(row[4]["returned_as"] == "due" for row in events(store, "ticket.unparked"))


def test_the_next_part_can_be_opened_after_the_one_before_landed_and_the_last_closes_the_ticket(tmp_path):
    hub, store, prs = world(tmp_path, part=(1, 2))
    merged(hub, prs[10])
    run(hub, store, prs[10])
    second = open_part(hub, store, "ticket-10-b", (2, 2))
    assert marks.part_of(hub.pull(REPO, second).body) == (2, 2)
    merged(hub, second)
    done = run(hub, store, second)
    assert (done.outcome, done.ticket) == ("closed", 10)
    held, issue = ticket(hub)
    assert not issue.open and issue.state_reason == "completed" and not held.park_lines
    assert f"#{second}" in last_comment(hub, 10) and "| 1 | pass |" in last_comment(hub, 10)


def test_a_merged_last_part_alone_closes_the_ticket_as_a_whole_pull_request_does(tmp_path):
    hub, store, prs = world(tmp_path, part=(2, 2))
    merged(hub, prs[10])
    assert run(hub, store, prs[10]).outcome == "closed"
    assert not ticket(hub)[1].open


def test_a_merged_part_pull_request_follow_run_twice_acts_once(tmp_path):
    hub, store, prs = world(tmp_path, part=(1, 2))
    merged(hub, prs[10])
    run(hub, store, prs[10])
    comments, calls = len(hub.comments(REPO, 10)), len(hub.calls)
    again = run(hub, store, prs[10])
    assert again.outcome == "already_landed" and len(hub.comments(REPO, 10)) == comments and len(hub.calls) == calls
    assert ticket(hub)[1].open


# closed unmerged


def test_closed_unmerged_sends_the_ticket_to_the_brain(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.close_pull(prs[10])
    done = run(hub, store, prs[10])
    assert done.outcome == "closed_unmerged"
    held, issue = ticket(hub)
    assert issue.open and "needs-triage" in held.labels and not held.parked
    assert ("step.ended", "pr", "failed", "closed_unmerged") in [row[:4] for row in events(store, "step.ended")]


# open: red


def test_red_on_the_head_returns_the_ticket_to_ready_with_the_failing_ids_and_counts_it(tmp_path):
    hub, store, prs = world(tmp_path)
    pr = prs[10]
    hub.checks_on(hub.head(pr), FAILED, failing=["tests/test_cart.py::test_quote"])
    done = run(hub, store, pr)
    assert done.outcome == "red"
    held, issue = ticket(hub)
    assert held.ready and not held.parked and "tests/test_cart.py::test_quote" in last_comment(hub, 10)
    assert marks.ready_since(hub.comments(REPO, pr)) is None
    counted = events(store, "attempt.counted")
    assert len(counted) == 1 and counted[0][4]["counted"] is True and counted[0][4]["in_a_row"] == 1
    assert ("step.ended", "pr", "failed", "red") in [row[:4] for row in events(store, "step.ended")]


def test_a_follow_run_twice_moves_the_ticket_once(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.checks_on(hub.head(prs[10]), FAILED, failing=["t::a"])
    run(hub, store, prs[10])
    comments = len(hub.comments(REPO, 10))
    assert run(hub, store, prs[10]).outcome == "idle" and len(hub.comments(REPO, 10)) == comments


def test_red_only_on_tests_red_on_main_parks_the_ticket_on_the_red_main_ticket_not_counted(tmp_path):
    hub, store, prs = world(tmp_path)
    title, body = red_main.build(["t::a", "t::b"], sha="m0", run_id="7", pr=None)
    red = hub.create_issue(REPO, title, body, list(red_main.LABELS))
    hub.checks_on(hub.head(prs[10]), FAILED, failing=["t::a"])
    done = run(hub, store, prs[10])
    assert done.outcome == "red_on_main"
    held, _ = ticket(hub)
    assert held.park_lines == (f"Parked: ticket_closed #{red} until 2026-11-09",) and held.parked
    assert events(store, "attempt.counted")[0][4]["counted"] is False


def test_red_on_a_test_main_does_not_fail_is_the_ticket_s_own(tmp_path):
    hub, store, prs = world(tmp_path)
    title, body = red_main.build(["t::a"], sha="m0", run_id="7", pr=None)
    hub.create_issue(REPO, title, body, list(red_main.LABELS))
    hub.checks_on(hub.head(prs[10]), FAILED, failing=["t::a", "t::mine"])
    assert run(hub, store, prs[10]).outcome == "red"


def test_the_third_red_in_a_row_goes_to_the_brain(tmp_path):
    hub, store, prs = world(tmp_path)
    pr = prs[10]
    for attempt in range(3):
        if attempt:
            from follow_world import ROWS_NO_AFTER, Git
            from orch_it.merge.pr_open import open_pr
            hub.push(pr)
            hub.later(minutes=10)
            open_pr(hub, store, REPO, 10, acceptance=ROWS_NO_AFTER, branch=f"ticket-10", base="main", git=Git(hub),
                    settings=MergeSettings(), now=hub.now)
        hub.checks_on(hub.head(pr), FAILED, failing=["t::mine"])
        done = run(hub, store, pr)
    assert done.outcome == "red_in_a_row"
    held, _ = ticket(hub)
    assert "needs-triage" in held.labels and not held.ready
    assert events(store, "attempt.counted")[-1][4]["in_a_row"] == 3


def test_a_red_without_a_report_is_no_result_and_is_run_once_more(tmp_path):
    hub, store, prs = world(tmp_path)
    run_id = hub.checks_on(hub.head(prs[10]), FAILED, failing=None)
    assert run(hub, store, prs[10]).outcome == "rerun"
    assert ("rerun", run_id) in hub.calls
    held, _ = ticket(hub)
    assert held.parked  # never an attempt, the ticket waits on


def test_a_cut_run_is_run_once_more_and_a_second_no_result_goes_to_the_brain(tmp_path):
    hub, store, prs = world(tmp_path)
    pr = prs[10]
    run_id = hub.checks_on(hub.head(pr), NO_RESULT)
    assert run(hub, store, pr).outcome == "rerun"
    assert run(hub, store, pr).outcome == "waiting"  # the rerun is pending
    hub.checks_on(hub.head(pr), NO_RESULT, run_id=run_id, attempt=2)
    done = run(hub, store, pr)
    assert done.outcome == "no_result"
    held, _ = ticket(hub)
    assert "needs-triage" in held.labels
    assert all(row[4]["counted"] is False for row in events(store, "attempt.counted"))  # never an attempt


def test_a_check_pending_past_its_bound_is_cancelled(tmp_path):
    hub, store, prs = world(tmp_path)
    run_id = hub.checks_on(hub.head(prs[10]), PENDING, started=hub.now)
    hub.later(minutes=61)
    assert run(hub, store, prs[10]).outcome == "stuck"
    assert ("cancel", run_id) in hub.calls


def test_a_green_of_an_older_head_is_not_read_for_the_new_one(tmp_path):
    hub, store, prs = world(tmp_path)
    pr = prs[10]
    hub.checks_on(hub.head(pr), FAILED, failing=["t::old"])
    hub.push(pr)
    assert run(hub, store, pr).outcome == "waiting"  # no check yet on the new head: never green, never red
    hub.checks_on(hub.head(pr), PASSED)
    assert run(hub, store, pr).outcome == "green"


def test_green_but_behind_waits_for_the_line(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.checks_on(hub.head(prs[10]), PASSED)
    hub.land()
    assert run(hub, store, prs[10]).outcome == "behind"


# open: people, edits, time


def test_a_person_s_comment_on_the_pull_request_leaves_the_line_and_sends_the_ticket_to_the_brain(tmp_path):
    hub, store, prs = world(tmp_path)
    pr = prs[10]
    hub.later(minutes=1)
    hub.note(pr, "Please do not merge yet, the rates change on Monday.", author="owner-x")
    done = run(hub, store, pr)
    assert done.outcome == "commented"
    assert marks.ready_since(hub.comments(REPO, pr)) is None
    held, _ = ticket(hub)
    assert "needs-triage" in held.labels and not held.parked
    rows = events(store, "pr.commented")
    assert len(rows) == 1 and rows[0][4]["author_role"] == "owner"


@pytest.mark.parametrize("author, bot, body", [
    ("github-actions", True, "A bot's summary"), ("owner-x", False, f"{marks.LAYER_MARK}\nThe layer's own note")])
def test_a_bot_s_or_the_layer_s_comment_is_no_person_s(tmp_path, author, bot, body):
    hub, store, prs = world(tmp_path)
    hub.later(minutes=1)
    hub.note(prs[10], body, author=author, bot=bot)
    assert run(hub, store, prs[10]).outcome == "waiting"


def test_a_stranger_who_pastes_the_layer_s_mark_is_still_a_person(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.later(minutes=1)
    hub.note(prs[10], marks.left_comment("red", "not really"), author="a-stranger")
    assert run(hub, store, prs[10]).outcome == "commented"


def test_a_comment_from_before_the_ready_mark_was_answered_by_it(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.notes[(REPO, prs[10])].insert(0, marks.Comment("owner-x", False, "an old question", hub.now - timedelta(hours=1)))
    assert run(hub, store, prs[10]).outcome == "waiting"


def test_a_person_s_comment_on_the_parked_ticket_stops_the_merge_too(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.later(minutes=1)
    hub.note(10, "Hold on, the zone list changed.", author="owner-x")
    assert run(hub, store, prs[10]).outcome == "unanswered"
    assert marks.ready_since(hub.comments(REPO, prs[10])) is None


def test_a_ticket_closed_while_its_pull_request_waits_takes_it_out_of_the_line(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.close(10, "not_planned")
    done = run(hub, store, prs[10])
    assert done.outcome == "withdrawn"
    assert marks.ready_since(hub.comments(REPO, prs[10])) is None
    assert ("step.ended", "pr", "stopped", "withdrawn") in [row[:4] for row in events(store, "step.ended")]


def test_a_ticket_edited_while_its_pull_request_waits_goes_back_to_ready_to_be_planned_again(tmp_path):
    hub, store, prs = world(tmp_path)
    issue = hub.issues[(REPO, 10)]
    hub.add(10, issue.body.replace("10115", "10117"), issue.labels)
    done = run(hub, store, prs[10])
    assert done.outcome == "edited"
    held, _ = ticket(hub)
    assert held.ready and not held.parked and marks.ready_since(hub.comments(REPO, prs[10])) is None


def test_open_past_its_bound_goes_to_the_brain(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.later(hours=25)
    done = run(hub, store, prs[10])
    assert done.outcome == "timeout"
    held, _ = ticket(hub)
    assert "needs-triage" in held.labels and marks.ready_since(hub.comments(REPO, prs[10])) is None


def test_a_conflict_returns_the_ticket_to_ready_with_the_files_not_counted(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.pulls[prs[10]].conflict = ("checkout/cart.py", "checkout/zones.py")
    done = run(hub, store, prs[10])
    assert done.outcome == "conflict"
    held, _ = ticket(hub)
    assert held.ready and "checkout/cart.py" in last_comment(hub, 10) and "checkout/zones.py" in last_comment(hub, 10)
    assert events(store, "attempt.counted")[0][4]["counted"] is False
    assert events(store, "pr.conflicted")[0][4]["files"] == ["checkout/cart.py", "checkout/zones.py"]


def test_a_pull_request_with_no_ticket_leaves_the_line_on_red_and_touches_no_issue(tmp_path):
    hub, store, _ = world(tmp_path)
    pr = hub.open_pull(body="A hand change")
    hub.comment(REPO, pr, marks.ready_comment(None, hub.head(pr), hub.now))
    hub.checks_on(hub.head(pr), FAILED, failing=["t::x"])
    writes = len(hub.writes)
    assert run(hub, store, pr).outcome == "red"
    assert marks.ready_since(hub.comments(REPO, pr)) is None and len(hub.writes) == writes


@pytest.mark.parametrize("event, expected", [
    ({"pull_request": {"number": 101}}, 101),
    ({"issue": {"number": 101, "pull_request": {"url": "x"}}, "comment": {"body": "?"}}, 101),
    ({"issue": {"number": 10}}, "park"),  # the ticket's park names its pull request
    ({"issue": {"number": 77}}, None), ({}, None), ({"issue": "x"}, None), ({"pull_request": {"number": "1"}}, None),
])
def test_the_event_names_the_pull_request_to_follow(tmp_path, event, expected):
    from orch_it.merge.follow import pr_of_event

    hub, _, prs = world(tmp_path)
    hub.add(77, "no park here")
    number, why = pr_of_event(event, hub, REPO)
    assert number == (prs[10] if expected == "park" else expected) and (why == "") == (number is not None)


def test_the_park_reader_hands_a_merged_pull_request_s_ticket_to_the_follow(tmp_path):
    from orch_it.tickets.reader import read_parks

    hub, store, prs = world(tmp_path)
    merged(hub, prs[10])
    reports = read_parks(hub, store, REPO, now=hub.now, follow=lambda pr: run(hub, store, pr).outcome)
    assert [(r.number, r.outcome, r.detail) for r in reports] == [(10, "followed", "closed")]
    assert not hub.issues[(REPO, 10)].open  # closed with its rows, never sent back to ready


def test_a_pull_request_that_lost_its_refs_line_still_closes_the_ticket_parked_on_it(tmp_path):
    hub, store, prs = world(tmp_path)
    pr = prs[10]
    hub.pulls[pr].body = "A person rewrote the description."
    merged(hub, pr)
    assert run(hub, store, pr).outcome == "closed" and not hub.issues[(REPO, 10)].open


def test_of_two_refs_lines_the_ticket_parked_on_the_pull_request_is_its_own(tmp_path):
    hub, store, prs = world(tmp_path)
    pr = prs[10]
    hub.add(9, BODY, ["ready-for-agent"])
    hub.pulls[pr].body = "Refs #9\nRefs #10\n"
    merged(hub, pr)
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("closed", 10) and hub.issues[(REPO, 9)].open


def test_a_merge_never_moves_a_ticket_that_waits_on_another_pull_request(tmp_path):
    hub, store, prs = world(tmp_path)
    old = hub.open_pull(body="Refs #10", branch="ticket-10-old")
    merged(hub, old)
    writes = len(hub.writes)
    done = run(hub, store, old)
    assert done.outcome == "merged_other" and len(hub.writes) == writes and hub.issues[(REPO, 10)].open


def test_the_close_of_a_parked_ticket_records_its_return(tmp_path):
    hub, store, prs = world(tmp_path)
    merged(hub, prs[10])
    run(hub, store, prs[10])
    assert [row[4]["returned_as"] for row in events(store, "ticket.unparked")] == ["due"]


def test_no_result_with_no_run_to_run_again_goes_to_the_brain_at_once(tmp_path):
    hub, store, prs = world(tmp_path)
    sha = hub.head(prs[10])
    hub.checks_on(sha, NO_RESULT)
    hub.check_runs[sha] = {name: replace(check, run_id=None) for name, check in hub.check_runs[sha].items()}
    assert run(hub, store, prs[10]).outcome == "no_result"
    assert "needs-triage" in ticket(hub)[0].labels


def test_a_red_whose_other_job_has_no_report_returns_the_ids_it_has(tmp_path):
    hub, store, prs = world(tmp_path)
    sha = hub.head(prs[10])
    hub.checks_on(sha, FAILED, names=(LINUX_CHECK,), failing=["t::linux_only"], run_id="r1")
    hub.checks_on(sha, FAILED, names=(WINDOWS_CHECK,), failing=None, run_id="r2")
    assert run(hub, store, prs[10]).outcome == "red"
    assert "t::linux_only" in last_comment(hub, 10)


def test_a_ticket_edited_while_the_follow_acts_is_left_as_it_is_and_the_next_follow_acts(tmp_path, monkeypatch):
    from orch_it.tickets import park

    hub, store, prs = world(tmp_path)
    pr = prs[10]
    hub.checks_on(hub.head(pr), FAILED, failing=["t::a"])
    real = park.write_unchanged
    raced = []

    def edited_meanwhile(*args, **kwargs):  # #398's write refuses: the ticket changed since it was read
        if not raced:
            raced.append(1)
            raise park.ParkError("ticket_changed", "edited while it was read")
        return real(*args, **kwargs)

    monkeypatch.setattr(park, "write_unchanged", edited_meanwhile)
    comments = len(hub.comments(REPO, 10))
    assert run(hub, store, pr).outcome == "raced"
    assert len(hub.comments(REPO, 10)) == comments and marks.ready_since(hub.comments(REPO, pr)) is not None
    assert ticket(hub)[0].parked  # nothing moved: the next follow acts on the ticket as it is then
    assert run(hub, store, pr).outcome == "red"


def test_a_red_main_ticket_closed_a_moment_before_the_park_returns_the_ticket_as_red(tmp_path):
    hub, store, prs = world(tmp_path)
    title, body = red_main.build(["t::a"], sha="m0", run_id="7", pr=None)
    red = hub.create_issue(REPO, title, body, list(red_main.LABELS))
    hub.checks_on(hub.head(prs[10]), FAILED, failing=["t::a"])
    from orch_it.tickets import park
    real = park.park

    def closed_first(*args, **kwargs):
        hub.close(red)
        return real(*args, **kwargs)

    park.park = closed_first
    try:
        done = run(hub, store, prs[10])
    finally:
        park.park = real
    assert done.outcome == "red"
    held, _ = ticket(hub)
    assert held.ready and not held.parked


def test_thousands_of_failing_ids_fit_one_comment(tmp_path):
    hub, store, prs = world(tmp_path)
    ids = [f"tests/test_{n}.py::test_{'x' * 250}" for n in range(3000)]
    hub.checks_on(hub.head(prs[10]), FAILED, failing=ids)
    run(hub, store, prs[10])
    said = last_comment(hub, 10)
    assert len(said) < 65_536 and "and 2900 more" in said


def test_a_person_s_comment_in_the_same_second_as_the_ready_mark_counts(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.note(prs[10], "wait", author="owner-x")
    assert run(hub, store, prs[10]).outcome == "commented"


def test_a_pull_request_out_of_the_line_is_idle(tmp_path):
    hub, store, _ = world(tmp_path)
    pr = hub.open_pull(body="A draft of an idea")
    hub.checks_on(hub.head(pr), FAILED, failing=["t::x"])
    assert run(hub, store, pr).outcome == "idle"
