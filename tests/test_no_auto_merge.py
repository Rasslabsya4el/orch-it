"""No GitHub auto-merge (#414 unit 7): nothing in `src/` turns it on, off or reads it; with the repo setting "Allow
auto-merge" off, a green pull request still merges, by the merge line itself, at the head sha whose required checks it
read, and never on a red head or on a head whose green checks belong to an older sha."""

import re
from pathlib import Path

from fake_github import REPO
from line_world import line_world

from orch_it.merge.gh import GhPullHub
from orch_it.merge.line import run_line
from orch_it.merge.settings import MergeSettings
from orch_it.testruns.github import GithubError

SRC = Path(__file__).resolve().parents[1] / "src"
REFUSED = "auto-merge is not allowed for this repository (HTTP 405)"


def line(hub, store, ci, tmp_path, **settings):
    return run_line(hub, store, REPO, "main", settings=MergeSettings(**settings), clock=lambda: hub.now,
                    sleep=ci.sleep, root=tmp_path / "locks", holder="test line")


def refuse_auto_merge(hub):
    """The repo setting off: GitHub refuses `gh pr merge --auto` (and its `--disable-auto`); every call is kept."""

    asked = []

    def refuse(repo, number, method):
        asked.append((number, method))
        raise GithubError("gh_failed", REFUSED)

    hub.set_auto_merge = refuse
    return asked


def merges(hub):
    return [call for call in hub.calls if call[0] == "merge"]


def test_nothing_in_src_names_auto_merge():
    found = [f"{path.relative_to(SRC)}:{number}" for path in sorted(SRC.rglob("*")) if path.suffix in (".py", ".json", ".md", ".sql")
             for number, text in enumerate(path.read_text("utf-8").splitlines(), 1)
             if re.search(r"--auto\b|auto_merge|auto-merge", text, re.IGNORECASE)]
    assert found == []


def test_the_gh_seam_merges_with_the_merge_endpoint_and_has_no_auto_merge_call():
    calls = []

    class Gh:
        def __call__(self, args):
            calls.append(list(args))
            return '{"merged": true, "sha": "abc123"}'

    hub = GhPullHub(Gh())
    assert not hasattr(hub, "set_auto_merge")
    hub.merge(REPO, 5, "deadbeef", "merge")
    assert calls and all("--auto" not in c and "--disable-auto" not in c for c in calls)


def test_a_green_pull_request_is_merged_by_the_line_after_pr_open_with_auto_merge_refused(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    asked = refuse_auto_merge(hub)
    pr = prs[10]
    assert hub.pull(REPO, pr).state == "open" and merges(hub) == []  # `pr open` merged nothing and set nothing
    done = line(hub, store, ci, tmp_path)
    assert [(s.pr, s.result) for s in done.served] == [(pr, "merged")]
    assert hub.pull(REPO, pr).state == "merged" and ci.merged == [pr]
    assert merges(hub) == [("merge", pr, "merge")]
    assert asked == [] and not [c for c in hub.calls if c[0] == "auto_merge"]


def test_a_pull_request_whose_required_checks_are_red_is_not_merged(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    asked = refuse_auto_merge(hub)
    ci.red[prs[10]] = ["tests/test_cart.py::test_quote"]
    done = line(hub, store, ci, tmp_path)
    assert [(s.pr, s.result, s.detail) for s in done.served] == [(prs[10], "left", "red")]
    assert hub.pull(REPO, prs[10]).state == "open" and merges(hub) == [] and asked == []


def test_a_pull_request_green_on_an_older_sha_but_not_on_its_head_is_not_merged(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    asked = refuse_auto_merge(hub)
    pr = prs[10]
    ci.sleep(10 * 60)  # the checks of the head it was opened at end green
    old = hub.head(pr)
    assert {c.state for c in hub.checks(REPO, old)} == {"passed"}
    hub.push(pr)  # a new head: its checks are still to run, and never end
    ci.never.add(pr)
    done = line(hub, store, ci, tmp_path, check_minutes=20)
    assert hub.head(pr) != old
    assert [(s.pr, s.result) for s in done.served] == [(pr, "bound")]
    assert hub.pull(REPO, pr).state == "open" and merges(hub) == [] and asked == []
