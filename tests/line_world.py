"""A repo with tickets in the merge line and a CI that runs on GitHub's clock (#400 unit 4).

`CI.tick` is what GitHub does while the line sleeps: a head with no check run gets one (pending),
and a pending run older than `minutes` ends (green, or red for the pull requests named in `red`).
Nothing merges but the line (#414). Every head that got a run is kept per pull request with whether it
was up to date when it ran.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from fake_github import PASSED, PENDING, FAILED, REPO, REQUIRED, FakeGitHub
from follow_world import NO_AFTER_MERGE, NOW, ROWS_NO_AFTER, TITLE, Git

from orch_it.events.store import Store
from orch_it.merge.pr_open import open_pr
from orch_it.merge.settings import MergeSettings


@dataclass
class CI:
    hub: FakeGitHub
    minutes: float = 5
    red: dict = field(default_factory=dict)  # pr -> failing test ids of its next run
    never: set = field(default_factory=set)  # prs whose runs never end
    slow: dict = field(default_factory=dict)  # pr -> minutes its runs take, when not `minutes`
    on_tick: list = field(default_factory=list)  # callables run at the end of each tick
    runs: dict = field(default_factory=dict)  # pr -> [(head sha, up to date when it ran)]
    merged: list = field(default_factory=list)  # pr numbers in merge order, by anyone

    def __post_init__(self):
        self.hub.on_merge.append(self.merged.append)

    def tick(self):
        hub = self.hub
        for number, pull in list(hub.pulls.items()):
            if pull.state != "open":
                continue
            sha = hub.head(number)
            if sha not in hub.check_runs:
                hub.checks_on(sha, PENDING, started=hub.now)
                self.runs.setdefault(number, []).append((sha, hub.pull(REPO, number).behind == 0))
        for sha, checks in hub.check_runs.items():
            number = next((n for n, p in hub.pulls.items() if hub.head(n) == sha), None)
            if number in self.never:
                continue
            for name, check in list(checks.items()):
                took = self.slow.get(number, self.minutes)
                if check.state == PENDING and hub.now - check.started_at >= timedelta(minutes=took):
                    failing = self.red.get(number)
                    hub.checks_on(sha, FAILED if failing else PASSED, run_id=check.run_id, attempt=check.attempt,
                                  failing=failing or (), started=check.started_at, names=(name,))
        for hook in list(self.on_tick):
            hook()

    def sleep(self, seconds):
        self.hub.later(seconds=seconds)
        self.tick()


def line_world(tmp_path, tickets=(10, 11, 12, 13), gap_seconds=10):
    """Tickets each with its pull request opened by `pr open` off the same main, `gap_seconds`
    apart: (hub, store, {ticket: pr}, ci)."""

    hub = FakeGitHub(NOW)
    hub.required_by_base = {"main": set(REQUIRED)}  # main requires every job
    store = Store.open(tmp_path / "events")
    ci = CI(hub)
    prs = {}
    for number in tickets:
        hub.add(number, NO_AFTER_MERGE, ["ready-for-agent"], title=TITLE)
        branch = f"ticket-{number}"
        hub.branches[branch] = [*hub.branches["main"], hub.sha(f"t{number}-")]
        prs[number] = open_pr(hub, store, REPO, number, acceptance=ROWS_NO_AFTER, branch=branch, base="main",
                              git=Git(hub), settings=MergeSettings(), now=hub.now).pr
        ci.sleep(gap_seconds)
    return hub, store, prs, ci
