"""The reader of parks (#398 decision 5, acceptance rows 4 and 5): `read_parks` reads every
parked ticket of a repo, reports it waiting, due, other way, late, unreadable, returned by a
person's comment (or failed, to be read again next run), acts on it and writes its events."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fake_hub import REPO, FakeHub
from ticket_fixtures import fixture

from orch_it.events.store import Store
from orch_it.testruns.github import GithubError
from orch_it.tickets import park as parks
from orch_it.tickets import reader
from orch_it.tickets.hub import LAYER_MARK

BODY = fixture("good_basic")[1]
PARKED_ON = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "events")


def parked(hub, number, line, *labels):
    return hub.add(number, f"{line}\n\n{BODY}", ["parked", "ready-for-agent", "priority", *labels])


def read(hub, store):
    return {report.number: report for report in reader.read_parks(hub, store, REPO, now=hub.now)}


def stored(store, *columns, kind=None):
    where = f" WHERE kind = '{kind}'" if kind else ""
    return [tuple(row) for row in store.query(
        f"SELECT ticket, {', '.join(columns)} FROM events{where} ORDER BY ticket, kind").rows]


def test_each_reader_outcome_is_reported_acted_on_and_written(store):
    hub = FakeHub(PARKED_ON)
    hub.add(12, is_pr=True)
    hub.add(13, is_pr=True)
    hub.add(14)
    parked(hub, 1, "Parked: pr_ended #12 until 2026-11-30")       # waiting
    parked(hub, 2, "Parked: pr_ended #13 until 2026-11-30")       # due: merged
    parked(hub, 3, "Parked: ticket_closed #14 until 2026-11-30")  # other way: closed not planned
    parked(hub, 4, "Parked: date 2026-11-20 until 2026-11-21")    # late by the time it is read
    parked(hub, 5, "Parked: waiting for the rates")              # unreadable: free text
    parked(hub, 6, "Parked: pr_ended #12 until 2026-11-30")       # returned by a person's comment
    hub.later(days=1)
    hub.close(13, merged=True)
    hub.close(14, "not_planned")
    hub.say(6, "Is this still needed?", author="a-person")
    hub.say(1, f"{LAYER_MARK}\nA note the layer wrote", author="owner-x")  # the layer's own: no return
    hub.say(2, "an old note", author="a-person", at=PARKED_ON - timedelta(hours=1))  # before the park

    hub.now = datetime(2026, 11, 22, 1, 0, tzinfo=UTC)  # #4's date came but its until has passed
    hub.close(13, merged=True)
    reports = read(hub, store)

    assert {n: r.outcome for n, r in reports.items()} == {
        1: "waiting", 2: "due", 3: "other_way", 4: "due", 5: "unreadable", 6: "comment"}
    # due: the line and the label off, the rest kept; it goes back to its queue with its priority
    assert set(hub.issues[(REPO, 2)].labels) == {"ready-for-agent", "priority"}
    assert hub.issues[(REPO, 2)].body == BODY
    # to the brain: off the queue, `needs-triage`, a comment saying why
    for number in (3, 5, 6):
        issue = hub.issues[(REPO, number)]
        assert set(issue.labels) == {"needs-triage", "priority"}
        assert issue.body == BODY
        assert LAYER_MARK in hub.notes[(REPO, number)][-1].body
    assert set(hub.issues[(REPO, 1)].labels) == {"parked", "ready-for-agent", "priority"}  # waiting: untouched

    assert stored(store, "step", "outcome", "reason", kind="step.ended") == [
        (2, "wait", "passed", None), (3, "wait", "failed", "other_way"), (4, "wait", "passed", None),
        (5, "wait", "no_result", "unreadable"), (6, "wait", "stopped", "comment")]
    assert stored(store, "returned_as", "\"by\"", "late_minutes", kind="ticket.unparked") == [
        (2, "due", "event", 0), (3, "other_way", "event", 0), (4, "due", "event", 60),
        (5, "unreadable", "event", None), (6, "comment", "event", 0)]
    assert stored(store, "reason", kind="park.unreadable") == [(5, "line_bad")]
    assert stored(store, "step", "run", kind="step.progressed") == [
        (1, "wait", f"park:{REPO}#1@{PARKED_ON.isoformat()}")]


def test_a_park_past_its_until_whose_event_has_not_come_is_late_to_the_brain(store):
    hub = FakeHub(PARKED_ON)
    hub.add(12, is_pr=True)
    parked(hub, 1, "Parked: pr_ended #12 until 2026-11-03")
    hub.now = datetime(2026, 11, 4, 2, 30, tzinfo=UTC)
    assert read(hub, store)[1].outcome == "late"
    assert set(hub.issues[(REPO, 1)].labels) == {"needs-triage", "priority"}
    assert stored(store, "returned_as", "late_minutes", kind="ticket.unparked") == [(1, "late", 150)]
    assert stored(store, "outcome", "reason", kind="step.ended") == [(1, "no_result", "late")]


@pytest.mark.parametrize("line, labels_extra, reason", [
    (None, (), "line_missing"),                                                            # PARK-4: label, no line
    ("Parked: pr_ended #12 until 2026-11-30\nParked: date 2026-11-20 until 2026-11-30", (), "lines_many"),  # PARK-3
    ("Parked: pr_ended #99 until 2026-11-30", (), "ref_missing"),
])
def test_a_park_nobody_can_return_is_unreadable_and_goes_to_the_brain(store, line, labels_extra, reason):
    hub = FakeHub(PARKED_ON)
    hub.add(12, is_pr=True)
    body = BODY if line is None else f"{line}\n\n{BODY}"
    hub.add(1, body, ["parked", *labels_extra])
    assert read(hub, store)[1].outcome == "unreadable"
    assert stored(store, "reason", kind="park.unreadable") == [(1, reason)]
    assert hub.issues[(REPO, 1)].body == BODY and "parked" not in hub.issues[(REPO, 1)].labels


def test_a_failed_read_of_one_ticket_leaves_it_parked_for_the_next_run_and_reads_the_rest(store):  # PARK-6
    hub = FakeHub(PARKED_ON)
    hub.add(12, is_pr=True, open=False, merged=True)
    parked(hub, 1, "Parked: pr_ended #12 until 2026-11-30")
    parked(hub, 2, "Parked: pr_ended #12 until 2026-11-30")
    hub.failing.add((REPO, 1))
    reports = read(hub, store)
    assert (reports[1].outcome, reports[2].outcome) == ("failed", "due")
    assert stored(store, "error_code", kind="error.raised") == [(1, "gh_failed")]
    hub.failing.clear()
    assert read(hub, store)[1].outcome == "due"  # the next run repairs it


def test_the_owner_s_answer_returns_an_owner_answer_park_and_another_person_s_comment_does_not(store):
    hub = FakeHub(PARKED_ON)
    parked(hub, 1, "Parked: owner_answer #1 until 2026-11-30")
    hub.later(hours=2)
    hub.say(1, "a guess from a passer-by", author="someone-else")
    assert read(hub, store)[1].outcome == "comment"
    parked(hub, 2, "Parked: owner_answer #2 until 2026-11-30")
    hub.later(hours=1)
    hub.say(2, "Take option B.", author="Owner-X")
    assert read(hub, store)[2].outcome == "due"


@pytest.mark.parametrize("kind_ref, world, outcome", [
    ("run_ended 501", {"run": parks.RunState(ended=False, passed=False)}, "waiting"),
    ("run_ended 501", {"run": parks.RunState(ended=True, passed=True)}, "due"),
    ("run_ended 501", {"run": parks.RunState(ended=True, passed=False)}, "other_way"),
    ("run_ended 501", {}, "unreadable"),
    ("layer_version 0.4.0", {"version": "0.3.9"}, "waiting"),
    ("layer_version 0.4.0", {"version": "0.4.0"}, "due"),
    ("layer_version 0.4.0", {}, "waiting"),
    ("date 2026-11-02", {}, "due"),
    ("ticket_closed #14", {"closed": "completed"}, "due"),
])
def test_each_kind_returns_on_its_event_the_right_way(store, kind_ref, world, outcome):
    hub = FakeHub(PARKED_ON)
    hub.add(14)
    if "run" in world:
        hub.runs[(REPO, "501")] = world["run"]
    if "version" in world:
        hub.versions[REPO] = world["version"]
    if "closed" in world:
        hub.close(14, world["closed"])
    parked(hub, 1, f"Parked: {kind_ref} until 2026-11-30")
    assert read(hub, store)[1].outcome == outcome


def test_two_reads_act_once(store):
    hub = FakeHub(PARKED_ON)
    hub.add(12, is_pr=True, open=False, merged=True)
    parked(hub, 1, "Parked: pr_ended #12 until 2026-11-30")
    read(hub, store)
    assert read(hub, store) == {}
    assert len(hub.notes[(REPO, 1)]) == 1


def test_a_return_keeps_every_line_but_the_park_s_own_with_its_line_ends(store):
    hub = FakeHub(PARKED_ON)
    hub.add(12, is_pr=True, open=False, merged=True)
    example = "## For the agent\r\nThe form:\r\n```\r\nParked: <kind> <ref> until <YYYY-MM-DD>\r\n```\r\nWeight: 2\r\n"
    hub.add(1, "﻿Parked: pr_ended #12 until 2026-11-30\r\n\r\n" + example, ["parked"])
    hub.add(2, "Parked: pr_ended #12 until 2026-11-30\r\rkept\rtoo", ["parked"])
    read(hub, store)
    assert hub.issues[(REPO, 1)].body == example
    assert hub.issues[(REPO, 2)].body == "kept\rtoo"


def test_a_park_line_with_no_label_is_found_and_goes_to_the_brain(store):  # PARK-4, the other way round
    hub = FakeHub(PARKED_ON)
    hub.add(12, is_pr=True)
    hub.add(1, f"Parked: pr_ended #12 until 2026-11-30\n\n{BODY}", ["ready-for-agent"])
    assert read(hub, store)[1].outcome == "unreadable"
    assert stored(store, "reason", kind="park.unreadable") == [(1, "label_missing")]
    assert hub.issues[(REPO, 1)].body == BODY and set(hub.issues[(REPO, 1)].labels) == {"needs-triage"}


class EditedWhileRead(FakeHub):
    """A GitHub where a person rewrites ticket 1 just after the reader first reads it."""

    def __init__(self, now):
        super().__init__(now)
        self.reads = 0

    def issue(self, repo, number):
        found = super().issue(repo, number)
        if number == 1:
            self.reads += 1
            if self.reads == 2:
                self.issues[(repo, 1)] = type(found)(**{**found.__dict__, "body": found.body + "\nA person's line."})
        return found if self.reads < 2 or number != 1 else super().issue(repo, number)


def test_a_ticket_edited_while_it_was_read_is_left_as_the_person_wrote_it(store):
    hub = EditedWhileRead(PARKED_ON)
    hub.add(12, is_pr=True, open=False, merged=True)
    parked(hub, 1, "Parked: pr_ended #12 until 2026-11-30")
    assert read(hub, store)[1].outcome == "failed"
    assert hub.issues[(REPO, 1)].body.endswith("A person's line.") and "parked" in hub.issues[(REPO, 1)].labels
    assert stored(store, "error_code", kind="error.raised") == [(1, "ticket_changed")]


class CommentFails(FakeHub):
    def comment(self, repo, number, body):
        raise GithubError("gh_failed", "HTTP 502")


def test_a_failed_comment_leaves_the_ticket_parked_for_the_next_run(store):
    hub = CommentFails(PARKED_ON)
    hub.add(12, is_pr=True, open=False, merged=True)
    parked(hub, 1, "Parked: pr_ended #12 until 2026-11-30")
    assert read(hub, store)[1].outcome == "failed"
    assert "parked" in hub.issues[(REPO, 1)].labels and hub.writes == []
    assert stored(store, "step", kind="step.ended") == []


def test_the_owner_of_an_org_repo_is_the_login_given(store):
    hub = FakeHub(PARKED_ON)
    parked(hub, 1, "Parked: owner_answer #1 until 2026-11-30")
    hub.later(hours=1)
    hub.say(1, "Option B.", author="The-Owner")
    reports = reader.read_parks(hub, store, REPO, now=hub.now, owner="the-owner")
    assert reports[0].outcome == "due"


def test_with_no_time_of_the_park_known_no_comment_counts_as_after_it(store):
    hub = FakeHub(PARKED_ON)
    hub.add(12, is_pr=True)
    parked(hub, 1, "Parked: pr_ended #12 until 2026-11-30")
    hub.label_times.clear()
    hub.say(1, "written long before", author="a-person", at=PARKED_ON - timedelta(days=30))
    assert read(hub, store)[1].outcome == "waiting"
