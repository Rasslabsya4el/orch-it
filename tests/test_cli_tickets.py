"""`python -m orch_it tickets park | unpark | parks` (#398 decisions 4 and 5): the commands print
their result, and a refusal prints `error: <code>` and exits 2."""

from datetime import UTC, datetime

import pytest
from fake_hub import REPO, FakeHub
from ticket_fixtures import fixture

from orch_it.cli import main
from orch_it.tickets import commands

BODY = fixture("good_basic")[1]


@pytest.fixture
def hub(monkeypatch):
    hub = FakeHub(datetime.now(UTC))
    hub.add(10, BODY, ["ready-for-agent"])
    hub.add(12, is_pr=True)
    monkeypatch.setattr(commands, "GhHub", lambda: hub)
    return hub


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_park_prints_the_line_it_wrote_and_parks_lists_it_waiting(hub, tmp_path, capsys):
    store = str(tmp_path / "events")
    code, out, err = run(capsys, "--store", store, "tickets", "park", "10", "pr_ended", "#12", "--until", "2099-01-01",
                         "--repo", REPO)
    assert (code, out, err) == (0, "Parked: pr_ended #12 until 2099-01-01\n", "")
    code, out, _ = run(capsys, "--store", store, "tickets", "parks", "--repo", REPO)
    assert code == 0
    assert out.splitlines() == ["ticket  outcome  park                                   detail",
                                "10      waiting  Parked: pr_ended #12 until 2099-01-01"]
    code, out, _ = run(capsys, "--store", store, "tickets", "unpark", "10", "--repo", REPO)
    assert (code, out) == (0, "unparked: Parked: pr_ended #12 until 2099-01-01\n")


def test_a_refused_park_says_its_code_and_exits_2(hub, tmp_path, capsys):
    hub.add(11)
    code, out, err = run(capsys, "--store", str(tmp_path / "events"), "tickets", "park", "10", "pr_ended", "#11",
                         "--until", "2099-01-01", "--repo", REPO)
    assert (code, out) == (2, "")
    assert err.startswith("error: ref_not_pr")


def test_a_park_needs_its_until(hub, tmp_path, capsys):
    code, _, err = run(capsys, "--store", str(tmp_path / "events"), "tickets", "park", "10", "date", "2099-01-01",
                       "--repo", REPO)
    assert code == 2 and "--until" in err
