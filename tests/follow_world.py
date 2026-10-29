"""A ticket with its pull request in the merge line, as `pr open` leaves them (#400): the world the
follow, the line and the holes table start from."""

from datetime import UTC, datetime

from fake_github import REPO, REQUIRED, FakeGitHub
from ticket_fixtures import fixture

from orch_it.events.store import Store
from orch_it.merge.pr_open import open_pr
from orch_it.merge.settings import MergeSettings

TITLE, BODY = fixture("good_basic")
NO_AFTER_MERGE = BODY.replace("| 4 after merge | abandoned carts at the payment step | under 30 % in the weekly report |\n",
                              "")
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
ROWS = """| row | result | evidence |
|---|---|---|
| 1 | pass | 4.90 = 4.90 |
| 2 | pass | 5.10 = 5.10 |
| 3 | pass | 7.30 = 7.30 |
| 4 | after merge | the weekly report |
"""
ROWS_NO_AFTER = "\n".join(line for line in ROWS.splitlines() if not line.startswith("| 4 ")) + "\n"


class Git:
    def __init__(self, hub):
        self.calls = []
        self.hub = hub

    def __call__(self, args):
        self.calls.append(args)
        return f"{self.hub.branches[args[-1].removesuffix('^{commit}')][-1]}\n" if args[0] == "rev-parse" else ""


def world(tmp_path, *, body=NO_AFTER_MERGE, rows=ROWS_NO_AFTER, tickets=(10,), now=NOW, part=None):
    """A hub with one open, ready ticket per number and its pull request opened by `pr open` (`part`: its (k, n), #434);
    returns (hub, store, {ticket: pr})."""

    hub = FakeGitHub(now)
    hub.required_by_base = {"main": set(REQUIRED)}  # main requires every job
    store = Store.open(tmp_path / "events")
    prs = {}
    for number in tickets:
        hub.add(number, body, ["ready-for-agent"], title=TITLE)
        branch = f"ticket-{number}"
        hub.branches[branch] = [*hub.branches["main"], hub.sha(f"t{number}-")]
        prs[number] = open_pr(hub, store, REPO, number, acceptance=rows, branch=branch, base="main", git=Git(hub),
                              settings=MergeSettings(), now=hub.now, part=part).pr
    return hub, store, prs


__all__ = ["BODY", "Git", "NO_AFTER_MERGE", "NOW", "REPO", "ROWS", "ROWS_NO_AFTER", "TITLE", "world"]
