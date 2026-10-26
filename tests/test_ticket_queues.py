"""The two queues a ticket waits in (orchestration-layer#403 decision 3; acceptance row 7): the
ready queue the dispatcher offers from (`ready-for-agent`, not parked, `priority` first) and the
sweep the brain works (`needs-triage`, `priority` first). Neither ever returns the brain's hand-off
issue, whether it is known by its label or by the `brain_handoff_issue` setting, whatever other
labels it carries."""

import pytest

from fake_hub import REPO, FakeHub
from orch_it import settings
from orch_it.brain import handoff
from orch_it.tickets.queues import queue


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    return path


def world() -> FakeHub:
    hub = FakeHub()
    hub.add(1, "", ["ready-for-agent"])
    hub.add(2, "", ["ready-for-agent", "priority"])
    hub.add(3, "", ["needs-triage"])
    hub.add(4, "", ["needs-triage", "priority"])
    hub.add(5, "", ["ready-for-agent", "parked"])
    hub.add(6, "", ["ready-for-agent"], is_pr=True)
    hub.add(7, "", ["needs-triage"], open=False)
    return hub


def test_ready_is_the_ready_tickets_priority_first_and_sweep_the_triage_ones():
    hub = world()
    assert [i.number for i in queue(hub, REPO, "ready")] == [2, 1]
    assert [i.number for i in queue(hub, REPO, "sweep")] == [4, 3]


@pytest.mark.parametrize("labels", [[handoff.LABEL, "ready-for-agent", "needs-triage", "priority"],
                                    ["ready-for-agent", "needs-triage"]])
def test_neither_queue_ever_returns_the_hand_off_issue(labels):
    hub = world()
    hub.add(9, "", labels, title=handoff.TITLE)
    settings.save_in_repo(REPO, "brain_handoff_issue", 9)
    for name in ("ready", "sweep"):
        assert 9 not in [i.number for i in queue(hub, REPO, name)]


def test_a_park_line_whose_label_was_lost_still_keeps_a_ticket_out_of_ready():
    hub = world()
    hub.add(8, "Parked: date 2099-01-01 until 2099-01-02\n\n## For the human\n", ["ready-for-agent"])
    assert 8 not in [i.number for i in queue(hub, REPO, "ready")]


def test_a_second_issue_carrying_the_label_is_left_out_too():
    hub = world()
    hub.add(12, "", [handoff.LABEL, "needs-triage"])
    assert 12 not in [i.number for i in queue(hub, REPO, "sweep")]


def test_a_bad_brain_title_setting_never_stops_a_queue():
    settings.save_in_repo(REPO, "brain_chat_title", "x" * 300)
    assert [i.number for i in queue(world(), REPO, "sweep")] == [4, 3]


def test_an_unknown_queue_is_refused():
    with pytest.raises(ValueError):
        queue(world(), REPO, "everything")
