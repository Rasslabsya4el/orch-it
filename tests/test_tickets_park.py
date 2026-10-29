"""The park form (#398 decision 4): one line `Parked: <kind> <ref> until <YYYY-MM-DD>`, written
and removed with the `parked` label by one command, and every hole of #384 (PARK-1..12) a
refusal of that command or a rule of the reader (decision 5)."""

from datetime import date

import pytest
from fake_hub import REPO, FakeHub
from ticket_fixtures import fixture

from orch_it.events.store import Store
from orch_it.tickets import park as parks

pytestmark = pytest.mark.usefixtures("ticket_language_ru")  # Russian ticket bodies (#413 unit 10a)

TODAY = date(2026, 11, 2)
BODY = fixture("good_basic")[1]


@pytest.fixture
def hub():
    hub = FakeHub()
    hub.add(10, BODY, ["ready-for-agent", "priority"])
    return hub


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "events")


def events(store, *columns):
    # by kind: the events of one write may share their `at`
    rows = store.query(f"SELECT kind, {', '.join(columns)} FROM events ORDER BY kind, at").rows
    return [tuple(row) for row in rows]


def park(hub, store, kind, ref, until="2026-11-30", number=10):
    return parks.park(hub, store, REPO, number, kind, ref, until, today=TODAY)


@pytest.mark.parametrize("line, kind, ref", [
    ("Parked: pr_ended #12 until 2026-11-30", "pr_ended", "#12"),
    ("Parked: ticket_closed acme-x/widgets-y#7 until 2026-11-30", "ticket_closed", "acme-x/widgets-y#7"),
    ("Parked: date 2026-11-20 until 2026-11-21", "date", "2026-11-20"),
    ("Parked: run_ended 18865432101 until 2026-11-30", "run_ended", "18865432101"),
    ("Parked: run_ended 019a2b3c-4d5e-7f60-8a9b-0c1d2e3f4a5b until 2026-11-30", "run_ended",
     "019a2b3c-4d5e-7f60-8a9b-0c1d2e3f4a5b"),
    ("Parked: owner_answer #398 until 2026-11-30", "owner_answer", "#398"),
    ("Parked: layer_version 0.4.0 until 2026-12-31", "layer_version", "0.4.0"),
])
def test_a_park_line_names_one_event_of_the_six_and_a_date(line, kind, ref):
    found = parks.read_line(line)
    assert (found.kind, found.ref, found.until) == (kind, ref, date.fromisoformat(line[-10:]))
    assert found.line == line


@pytest.mark.parametrize("line, code", [
    ("Parked: waiting for the rates to land", "kind_unknown"),          # PARK-1: free text
    ("Parked: pr_merged #12 until 2026-11-30", "kind_unknown"),
    ("Parked: pr_ended #12", "until_bad"),                                # PARK-5: no until
    ("Parked: pr_ended #12 until soon", "until_bad"),
    ("Parked: pr_ended #12 until 2026-02-30", "until_bad"),
    ("Parked: pr_ended #12 and ticket_closed #9 until 2026-11-30", "ref_bad"),  # PARK-3: two events
    ("Parked: pr_ended 12 until 2026-11-30", "ref_bad"),
    ("Parked: date tomorrow until 2026-11-30", "ref_bad"),
    ("Parked: run_ended #4 until 2026-11-30", "ref_bad"),
    ("Parked: layer_version v1 until 2026-11-30", "ref_bad"),
    ("Parked: date 2026-12-05 until 2026-12-01", "date_after_until"),
    ("parked: pr_ended #12 until 2026-11-30", "kind_unknown"),
    ("Parked:", "kind_unknown"),
])
def test_anything_but_the_one_form_is_refused_with_its_reason(line, code):
    with pytest.raises(parks.ParkError) as refused:
        parks.read_line(line)
    assert refused.value.code == code


def test_a_park_writes_its_line_and_the_label_in_one_write_and_keeps_the_priority(hub, store):
    hub.add(12, is_pr=True)
    written = park(hub, store, "pr_ended", "#12")

    assert written.line == "Parked: pr_ended #12 until 2026-11-30"
    assert len(hub.writes) == 1  # PARK-4: label and line together, one request
    issue = hub.issues[(REPO, 10)]
    assert issue.body == "Parked: pr_ended #12 until 2026-11-30\n\n" + BODY
    assert set(issue.labels) == {"ready-for-agent", "priority", "parked"}  # PARK-11
    run = f"park:{REPO}#10@{hub.now.isoformat()}"
    assert events(store, "ticket", "step", "run", "\"on\"", "on_ref", "until", "reason") == [
        ("step.started", 10, "wait", run, "pr_ended #12", None, None, "parked"),
        ("ticket.parked", 10, None, run, "pr_ended", "#12", "2026-11-30", None)]


@pytest.mark.parametrize("kind, ref, until, code", [
    ("pr_ended", "#12", "2026-11-01", "until_past"),                  # PARK-5
    ("pr_ended", "#12", "next week", "until_bad"),
    ("waiting", "the rates", "2026-11-30", "kind_unknown"),           # PARK-1
    ("pr_ended", "#12 #13", "2026-11-30", "ref_bad"),                 # PARK-3
    ("pr_ended", "#99", "2026-11-30", "ref_missing"),
    ("pr_ended", "#11", "2026-11-30", "ref_not_pr"),                  # MERGE-14: the kind names a PR
    ("ticket_closed", "#12", "2026-11-30", "ref_not_ticket"),
    ("ticket_closed", "#10", "2026-11-30", "ref_self"),
    ("ticket_closed", "#5", "2026-11-30", "ref_parent"),              # PARK-10
    ("ticket_closed", "#13", "2026-11-30", "event_passed"),
    ("pr_ended", "#14", "2026-11-30", "event_passed"),
    ("date", "2026-11-02", "2026-11-30", "event_passed"),
    ("run_ended", "777", "2026-11-30", "ref_missing"),
    ("run_ended", "778", "2026-11-30", "event_passed"),
    ("layer_version", "0.3.0", "2026-11-30", "event_passed"),
])
def test_a_park_that_cannot_return_is_refused_at_write_with_its_reason(hub, store, kind, ref, until, code):
    hub.add(5)
    hub.parents[(REPO, 10)] = (REPO, 5)
    hub.add(11)
    hub.add(12, is_pr=True)
    hub.add(13, open=False, state_reason="completed")
    hub.add(14, is_pr=True, open=False, merged=True)
    hub.runs[(REPO, "778")] = parks.RunState(ended=True, passed=False)
    hub.versions[REPO] = "0.3.0"
    with pytest.raises(parks.ParkError) as refused:
        park(hub, store, kind, ref, until)
    assert refused.value.code == code
    assert hub.writes == []
    assert events(store, "step", "outcome", "reason") == [
        ("step.ended", "wait", "refused", code), ("step.started", "wait", None, "parked")]


def test_a_second_park_is_refused_one_event_per_park(hub, store):  # PARK-3
    hub.add(12, is_pr=True)
    hub.add(20)
    park(hub, store, "pr_ended", "#12")
    with pytest.raises(parks.ParkError) as refused:
        park(hub, store, "ticket_closed", "#20")
    assert refused.value.code == "already_parked"
    assert len(hub.writes) == 1


def test_a_closed_ticket_or_one_that_is_not_there_is_not_parked(hub, store):
    hub.add(30, BODY, open=False, state_reason="completed")
    hub.add(31, BODY, is_pr=True)
    for number, code in ((30, "ticket_not_open"), (31, "ticket_missing"), (32, "ticket_missing")):
        with pytest.raises(parks.ParkError) as refused:
            park(hub, store, "date", "2026-11-20", number=number)
        assert refused.value.code == code


def test_a_park_on_a_store_run_needs_the_run_started_and_not_ended(hub, store):
    run = "019a2b3c-4d5e-7f60-8a9b-0c1d2e3f4a5b"
    with pytest.raises(parks.ParkError) as refused:
        park(hub, store, "run_ended", run)
    assert refused.value.code == "ref_missing"
    with store.writer() as writer:
        writer.write({"kind": "step.started", "repo": REPO, "ticket": None, "role": "script", "session": None,
                      "parent_session": None, "turn": None, "step": "test_run", "run": run, "note": None,
                      "detail": {"place": "own"}})
    assert park(hub, store, "run_ended", run).ref == run


def test_unpark_takes_the_line_and_the_label_off_together_and_keeps_the_rest(hub, store):
    hub.add(12, is_pr=True)
    park(hub, store, "pr_ended", "#12")
    taken = parks.unpark(hub, store, REPO, 10, now=hub.now)

    assert taken.line == "Parked: pr_ended #12 until 2026-11-30"
    issue = hub.issues[(REPO, 10)]
    assert issue.body == BODY
    assert set(issue.labels) == {"ready-for-agent", "priority"}
    assert len(hub.writes) == 2
    run = f"park:{REPO}#10@{hub.now.isoformat()}"
    ended = [row for row in events(store, "step", "outcome", "reason", "\"by\"", "returned_as", "run")
             if row[0] in ("step.ended", "ticket.unparked")]
    assert ended == [("step.ended", "wait", "stopped", "hand", None, None, run),
                     ("ticket.unparked", None, None, None, "hand", "hand", run)]
    with pytest.raises(parks.ParkError) as refused:
        parks.unpark(hub, store, REPO, 10, now=hub.now)
    assert refused.value.code == "not_parked"


def test_a_ref_naming_the_ticket_in_another_letter_case_is_the_ticket_itself(hub, store):
    hub.add(5)
    hub.parents[(REPO, 10)] = (REPO.upper(), 5)
    with pytest.raises(parks.ParkError) as refused:
        park(hub, store, "ticket_closed", f"{REPO.upper()}#10")
    assert refused.value.code == "ref_self"
    with pytest.raises(parks.ParkError) as refused:
        park(hub, store, "ticket_closed", "#5")
    assert refused.value.code == "ref_parent"
    with pytest.raises(parks.ParkError) as refused:
        parks.read_line("Parked: ticket_closed owner-x/..#5 until 2026-11-30")
    assert refused.value.code == "ref_bad"


def test_a_body_with_a_bom_keeps_its_human_block_first_when_parked(hub, store):
    hub.add(40, "﻿" + BODY)
    hub.add(12, is_pr=True)
    park(hub, store, "pr_ended", "#12", number=40)
    from orch_it import tickets

    parked_body = hub.issues[(REPO, 40)].body
    assert parked_body == "Parked: pr_ended #12 until 2026-11-30\n\n" + BODY
    assert tickets.violations(tickets.parse(parked_body)) == []


def test_a_park_that_would_pass_github_s_body_bound_is_refused(hub, store):
    hub.add(41, "x" * (parks.BODY_MAX - 10))
    hub.add(12, is_pr=True)
    with pytest.raises(parks.ParkError) as refused:
        park(hub, store, "pr_ended", "#12", number=41)
    assert refused.value.code == "body_too_long"
    assert hub.writes == []


def test_every_refusal_written_to_the_store_is_a_code_of_the_closed_set():
    from orch_it.events import schema

    assert set(parks.PARK_ERROR_CODES) - {"not_parked"} <= schema.codes_of("wait.ended", "reason")
    with pytest.raises(ValueError):
        parks.ParkError("not_a_code")
