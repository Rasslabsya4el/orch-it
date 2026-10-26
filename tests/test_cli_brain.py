"""`python -m orch_it brain handoff ensure | write` and `tickets queue` (orchestration-layer#403
decision 3): the commands print their result; a refusal prints `error: <code>` and exits 2."""

import pytest
from fake_hub import REPO, FakeHub

from orch_it import settings
from orch_it.brain import commands
from orch_it.cli import main
from orch_it.tickets import commands as ticket_commands


@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    hub = FakeHub()
    hub.add(1, "", ["ready-for-agent"])
    hub.add(2, "", ["needs-triage", "priority"])
    monkeypatch.setattr(commands, "GhHub", lambda: hub)
    monkeypatch.setattr(ticket_commands, "GhHub", lambda: hub)
    return hub


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_ensure_prints_what_it_did_and_a_second_run_nothing(hub, tmp_path, capsys):
    code, out, err = run(capsys, "brain", "handoff", "ensure", "--repo", REPO)
    assert (code, err) == (0, "") and out == "brain hand-off #3: created, pinned, setting_written\n"
    code, out, _ = run(capsys, "brain", "handoff", "ensure", "--repo", REPO)
    assert (code, out) == (0, "brain hand-off #3: nothing to change\n")


def test_write_posts_the_file_s_text_and_a_missing_home_is_refused(hub, tmp_path, capsys):
    text = tmp_path / "handoff.md"
    text.write_text("Done: the port. Next: the table.\n", "utf-8")
    store = str(tmp_path / "events")
    code, _, err = run(capsys, "--store", store, "brain", "handoff", "write", "--file", str(text), "--repo", REPO)
    assert code == 2 and err.startswith("error: handoff_issue_unset")
    run(capsys, "brain", "handoff", "ensure", "--repo", REPO)
    code, out, _ = run(capsys, "--store", store, "brain", "handoff", "write", "--file", str(text), "--ticket", "1",
                       "--repo", REPO)
    assert (code, out) == (0, "hand-off written on #3, pointer on #1\n")
    assert hub.notes[(REPO, 3)][-1].body.startswith("Done: the port.")


def test_a_hand_off_file_that_cannot_be_read_is_refused(hub, tmp_path, capsys):
    settings.save_in_repo(REPO, "brain_handoff_issue", 1)
    code, _, err = run(capsys, "brain", "handoff", "write", "--file", str(tmp_path / "none.md"), "--repo", REPO)
    assert code == 2 and err.startswith("error: handoff_unreadable")


def test_the_queues_print_their_tickets_in_order(hub, capsys):
    code, out, _ = run(capsys, "tickets", "queue", "sweep", "--repo", REPO)
    assert code == 0 and out.splitlines()[1:] == ["2       A ticket  needs-triage,priority"]
    code, out, _ = run(capsys, "tickets", "queue", "ready", "--repo", REPO)
    assert out.splitlines()[0].split() == ["ticket", "title", "labels"] and out.splitlines()[1].startswith("1 ")
