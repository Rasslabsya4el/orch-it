"""The table of the twelve park holes of #384 (`model.js` PARK-1..PARK-12; #398 acceptance row 4):
each is refused at write with its reason, or handled by the reader as #384 says. The hook's
refusal of a hand edit of the line or the label (PARK-1, PARK-4) is in `test_tickets_hook.py`."""

from datetime import UTC, date, datetime

import pytest
from fake_hub import REPO, FakeHub
from ticket_fixtures import fixture

from orch_it import tickets
from orch_it.events.store import Store
from orch_it.tickets import park as parks
from orch_it.tickets import reader

pytestmark = pytest.mark.usefixtures("ticket_language_ru")  # Russian ticket bodies (#413 unit 10a)

BODY = fixture("good_basic")[1]
TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def world():
    hub = FakeHub(NOW)
    hub.add(10, BODY, ["ready-for-agent", "priority"])
    hub.add(12, is_pr=True)
    hub.add(14)
    hub.add(5)
    hub.parents[(REPO, 10)] = (REPO, 5)
    return hub


def refused(hub, store, kind, ref, until="2026-11-30"):
    with pytest.raises(parks.ParkError) as caught:
        parks.park(hub, store, REPO, 10, kind, ref, until, today=TODAY)
    return caught.value.code


def outcome(hub, store, number=10):
    return {r.number: r.outcome for r in reader.read_parks(hub, store, REPO, now=hub.now)}.get(number)


def hand_parked(hub, line, labels=("parked",)):
    hub.add(10, f"{line}\n\n{BODY}" if line else BODY, labels)


def park_1(hub, store):  # one machine-readable form; anything else refused at write
    assert refused(hub, store, "waiting", "for the rates") == "kind_unknown"
    hand_parked(hub, "Parked: until the rates land")
    assert outcome(hub, store) == "unreadable" and "needs-triage" in hub.issues[(REPO, 10)].labels


def park_2(hub, store):  # one form, one reader: what the command writes is what the reader reads
    written = parks.park(hub, store, REPO, 10, "pr_ended", "#12", "2026-11-30", today=TODAY)
    assert parks.read_line(tickets.parse(hub.issues[(REPO, 10)].body).park_lines[0]) == written
    assert outcome(hub, store) == "waiting"


def park_3(hub, store):  # one event per park; two refused
    assert refused(hub, store, "pr_ended", "#12 and ticket_closed #14") == "ref_bad"
    parks.park(hub, store, REPO, 10, "pr_ended", "#12", "2026-11-30", today=TODAY)
    assert refused(hub, store, "ticket_closed", "#14") == "already_parked"
    hand_parked(hub, "Parked: pr_ended #12 until 2026-11-30\nParked: ticket_closed #14 until 2026-11-30")
    assert outcome(hub, store) == "unreadable"


def park_4(hub, store):  # label and form written together by one command; a mismatch refused
    parks.park(hub, store, REPO, 10, "pr_ended", "#12", "2026-11-30", today=TODAY)
    assert len(hub.writes) == 1 and "parked" in hub.writes[0][3] and hub.writes[0][2].startswith("Parked:")
    hand_parked(hub, None)  # the label set by hand, no line
    assert outcome(hub, store) == "unreadable"


def park_5(hub, store):  # every park carries until; one that never comes ends late
    with pytest.raises(parks.ParkError) as caught:
        parks.read_line("Parked: pr_ended #12")
    assert caught.value.code == "until_bad"
    assert refused(hub, store, "pr_ended", "#12", "2026-11-01") == "until_past"
    parks.park(hub, store, REPO, 10, "pr_ended", "#12", "2026-11-03", today=TODAY)
    hub.now = datetime(2026, 11, 4, 0, 30, tzinfo=UTC)
    assert outcome(hub, store) == "late"


def park_6(hub, store):  # each run re-reads everything; a failed repair retries next run
    parks.park(hub, store, REPO, 10, "pr_ended", "#12", "2026-11-30", today=TODAY)
    hub.close(12, merged=True)
    hub.failing.add((REPO, 10))
    assert outcome(hub, store) == "failed" and "parked" in hub.issues[(REPO, 10)].labels
    hub.failing.clear()
    assert outcome(hub, store) == "due"


def park_7(hub, store, tmp_path):  # the return needs no process of the session that parked
    parks.park(hub, store, REPO, 10, "pr_ended", "#12", "2026-11-30", today=TODAY)
    hub.close(12, merged=True)
    elsewhere = Store.open(tmp_path / "another-machine")  # a reader that never saw the park written
    assert outcome(hub, elsewhere) == "due"
    [started] = store.query("SELECT run FROM events WHERE kind = 'step.started'").rows
    [ended] = elsewhere.query("SELECT run FROM events WHERE kind = 'step.ended'").rows
    assert started == ended  # both name the same `wait` step


def park_8(hub, store):  # "parked" means one thing; the PR wait is the pr_ended form
    title_codes = [v.code for v in tickets.violations(tickets.parse(BODY, title="Rates (parked on the PR)"))]
    assert title_codes == ["title_parked"]
    assert parks.park(hub, store, REPO, 10, "pr_ended", "#12", "2026-11-30", today=TODAY).kind == "pr_ended"


def park_9(hub, store):  # a person's comment on a parked ticket returns it to the brain
    parks.park(hub, store, REPO, 10, "pr_ended", "#12", "2026-11-30", today=TODAY)
    hub.later(minutes=5)
    hub.say(10, "Do we still need this?", author="a-person")
    assert outcome(hub, store) == "comment" and "needs-triage" in hub.issues[(REPO, 10)].labels


def park_10(hub, store):  # closed not planned is the other way; waiting on one's own parent refused
    assert refused(hub, store, "ticket_closed", "#5") == "ref_parent"
    parks.park(hub, store, REPO, 10, "ticket_closed", "#14", "2026-11-30", today=TODAY)
    hub.close(14, "not_planned")
    assert outcome(hub, store) == "other_way"


def park_11(hub, store):  # priority is kept while parked and when it returns
    parks.park(hub, store, REPO, 10, "pr_ended", "#12", "2026-11-30", today=TODAY)
    assert "priority" in hub.issues[(REPO, 10)].labels
    hub.close(12, merged=True)
    assert outcome(hub, store) == "due"
    assert tickets.parse(hub.issues[(REPO, 10)].body, labels=hub.issues[(REPO, 10)].labels).priority


def park_12(hub, store):  # a park on a run carries until; a dead run ends late
    hub.runs[(REPO, "601")] = parks.RunState(ended=False, passed=False)
    parks.park(hub, store, REPO, 10, "run_ended", "601", "2026-11-05", today=TODAY)
    hub.now = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)  # the run never ended
    assert outcome(hub, store) == "late"


HOLES = {f"PARK-{n}": case for n, case in enumerate(
    (park_1, park_2, park_3, park_4, park_5, park_6, park_7, park_8, park_9, park_10, park_11, park_12), start=1)}


@pytest.mark.parametrize("hole", list(HOLES))
def test_each_park_hole_is_refused_at_write_or_handled_by_the_reader(hole, tmp_path):
    store = Store.open(tmp_path / "events")
    case = HOLES[hole]
    if case is park_7:
        case(world(), store, tmp_path)
    else:
        case(world(), store)


def test_the_table_holds_all_twelve():
    assert list(HOLES) == [f"PARK-{n}" for n in range(1, 13)]
