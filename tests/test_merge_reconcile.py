"""The scheduled sweep (#400 decisions 3 and 4; row 6 on fixture GitHub): `reconcile` repairs from
GitHub alone what no event fixed: a merged pull request whose follow never ran closes its ticket,
a member of the line is followed, red main gets its ticket; it runs the same with Actions off, and
on the machine brings the workflows' own events into the store."""

import json
from pathlib import Path

from fake_github import FAILED, PASSED, REPO, FakeGitHub
from follow_world import NOW, world

from orch_it.events.store import Store
from orch_it.merge import marks, reconcile as reconcile_module
from orch_it.merge.follow import FollowReport
from orch_it.merge.reconcile import reconcile
from orch_it.merge.settings import MergeSettings
from required_names import LINUX_CHECK, WINDOWS_CHECK


def sweep(hub, store, tmp_path, **kwargs):
    return reconcile(hub, store, REPO, "main", settings=MergeSettings(), now=hub.now, **kwargs)


def merged(hub, pr):
    hub.checks_on(hub.head(pr), PASSED)
    assert hub.merge(REPO, pr, hub.head(pr), "merge")


def test_a_merged_pull_request_whose_follow_never_ran_has_its_ticket_closed(tmp_path):
    hub, store, prs = world(tmp_path)
    merged(hub, prs[10])
    done = sweep(hub, store, tmp_path)
    assert not hub.issues[(REPO, 10)].open
    assert ("closed", f"#{prs[10]}") in done.repairs
    rows = store.query("SELECT outcome, json_extract(detail, '$.repairs') FROM events "
                       "WHERE kind = 'step.ended' AND step = 'reconcile'").rows
    assert [(outcome, json.loads(repairs)) for outcome, repairs in rows] == [("passed", ["closed"])]


def test_with_actions_off_the_same_sweep_runs_here_and_says_so(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.actions = False
    merged(hub, prs[10])
    done = sweep(hub, store, tmp_path)
    assert done.actions is False and not hub.issues[(REPO, 10)].open


def test_a_member_of_the_line_is_followed_and_red_main_gets_its_ticket(tmp_path):
    hub, store, prs = world(tmp_path)
    hub.checks_on(hub.head(prs[10]), FAILED, failing=["t::mine"])
    hub.main_run(hub.land(), {LINUX_CHECK: FAILED, WINDOWS_CHECK: PASSED}, failing=["t::main"])
    done = sweep(hub, store, tmp_path)
    assert ("unparked", f"#{prs[10]}") in done.repairs
    assert any(code == "red_ticket_opened" for code, _ in done.repairs)


def test_a_follow_or_a_main_check_that_breaks_never_ends_the_sweep_unwritten(tmp_path, monkeypatch):
    from orch_it.merge import reconcile as sweep_module
    from orch_it.testruns.github import GithubError

    hub, store, prs = world(tmp_path)

    def broken(*args, **kwargs):
        raise ValueError("a bug")

    def no_runs(repo, branch):
        raise GithubError("gh_failed", "Actions are disabled (HTTP 403)")

    monkeypatch.setattr(sweep_module, "follow", broken)
    hub.branch_runs = no_runs
    done = sweep(hub, store, tmp_path)
    assert len(done.failed) == 2 and done.repairs == []
    assert store.query("SELECT outcome FROM events WHERE kind = 'step.ended' AND step = 'reconcile'").rows == [
        ("failed",)]


def test_a_rerun_the_sweep_asked_for_is_a_repair(tmp_path):
    from fake_github import NO_RESULT

    hub, store, _ = world(tmp_path)
    hub.main_run(hub.land(), {LINUX_CHECK: NO_RESULT, WINDOWS_CHECK: PASSED})
    assert ("rerun", "main") in sweep(hub, store, tmp_path).repairs


def test_an_artifact_already_brought_in_is_not_downloaded_again(tmp_path):
    hub, store, _ = world(tmp_path)
    hub.event_files["orch-it-events-main-3-1"] = {"2026-11-02/abcd-runner.jsonl": ""}
    downloads = []
    real = hub.download_artifact
    hub.download_artifact = lambda *args: (downloads.append(args[2]), real(*args))
    sweep(hub, store, tmp_path, bring_events=True)
    sweep(hub, store, tmp_path, bring_events=True)
    assert downloads == ["orch-it-events-main-3-1"]


def test_a_sweep_with_nothing_to_repair_repairs_nothing(tmp_path):
    hub, store, _ = world(tmp_path)
    assert sweep(hub, store, tmp_path).repairs == []


def test_the_workflows_events_are_brought_into_the_machine_store_once(tmp_path):
    hub, store, _ = world(tmp_path)
    event = {"id": "01JZZZZZZZZZZZZZZZZZZZZZZZ", "kind": "pr.commented", "at": "2026-11-02T09:00:00+00:00",
             "repo": REPO, "ticket": 10, "role": "github", "session": None, "parent_session": None, "turn": None,
             "step": None, "run": None, "machine": "runner", "os": "linux", "layer_version": "0.0.0", "note": None,
             "detail": {"pr": 101, "author_role": "owner", "answered": False}}
    hub.event_files["orch-it-events-follow-7-1"] = {"2026-11-02/abcd1234-runner.jsonl": json.dumps(event) + "\n",
                                                    "2026-11-02/x.txt": "no", "elsewhere/y.jsonl": "{}\n"}
    done = sweep(hub, store, tmp_path, bring_events=True)
    assert done.imported == 1
    assert store.query("SELECT kind, machine FROM events WHERE kind = 'pr.commented'").rows == [
        ("pr.commented", "runner")]
    assert not (Path(store.root) / "elsewhere").exists()  # only a store's own day folders are brought in
    assert sweep(hub, store, tmp_path, bring_events=True).imported == 0


def test_the_workflow_watch_runs_after_the_main_check_and_opens_its_ticket(tmp_path, monkeypatch):
    from orch_it.merge import reconcile as sweep_module
    from orch_it.tickets.reader import TRIAGE_LABEL

    hub, store, prs = world(tmp_path)
    merged(hub, prs[10])
    order = []
    real_main, real_watch = sweep_module.check_main, sweep_module.watch_workflows

    def main(*args, **kwargs):
        order.append("main")
        return real_main(*args, **kwargs)

    def watch(*args, **kwargs):
        order.append("watch")
        return real_watch(*args, **kwargs)

    monkeypatch.setattr(sweep_module, "check_main", main)
    monkeypatch.setattr(sweep_module, "watch_workflows", watch)
    hub.end_run("example-flow", 4117, "failure")
    done = sweep(hub, store, tmp_path)
    assert order == ["main", "watch"]
    assert len(hub.issues_with_label(REPO, TRIAGE_LABEL)) == 1
    assert done.failed == []


def test_a_workflow_watch_that_breaks_never_stops_the_sweep(tmp_path, monkeypatch):
    from orch_it.merge import reconcile as sweep_module

    hub, store, prs = world(tmp_path)
    merged(hub, prs[10])

    def broken(*args, **kwargs):
        raise ValueError("a bug")

    monkeypatch.setattr(sweep_module, "watch_workflows", broken)
    done = sweep(hub, store, tmp_path)
    assert not hub.issues[(REPO, 10)].open
    assert ("closed", f"#{prs[10]}") in done.repairs
    assert len(done.failed) == 1 and done.failed[0].startswith("workflows:")
    assert store.query("SELECT outcome FROM events WHERE kind = 'step.ended' AND step = 'reconcile'").rows == [
        ("failed",)]


def test_a_sweep_on_the_workflow_token_runs_though_the_actions_setting_is_refused_to_it(tmp_path):
    """The real hub over a scripted `gh`: the workflow's own token has no administration scope, so
    `repos/<r>/actions/permissions` answers 403 there; the sweep still ends, as with a login that reads it."""

    from orch_it.merge.gh import GhPullHub
    from orch_it.testruns.github import GithubError

    _, store, _ = world(tmp_path)
    calls = []

    def gh(args):
        calls.append(args[1])
        path = args[args.index("api") + 1 if "--paginate" not in args else args.index("--paginate") + 1]
        if path.endswith("/actions/permissions"):
            raise GithubError("gh_failed", f"api {path}: gh: Resource not accessible by integration (HTTP 403)")
        if "search/issues" in args:
            return json.dumps({"items": []})
        if path.startswith(f"repos/{REPO}/actions/"):
            return json.dumps({"workflows": [], "workflow_runs": []})
        if path == f"repos/{REPO}":
            return json.dumps({"default_branch": "main"})
        return "[]"  # the issue and pull request lists

    done = reconcile(GhPullHub(gh), store, REPO, "main", settings=MergeSettings(), now=NOW)
    assert done.actions is True and done.failed == []
    assert f"repos/{REPO}/actions/permissions" in calls


def test_a_sweep_that_follows_a_skills_pull_request_closed_by_its_follow_repairs_closed_not_unparked(tmp_path, monkeypatch):
    """`closed_skills` maps to the repair `closed`, as a closed ticket does. A merged skills pull request is not
    listed by the sweep itself (it lists open pull requests), so the follow is replaced by its outcome here."""

    hub = FakeGitHub(NOW)
    store = Store.open(tmp_path / "events")
    pr = hub.open_pull(body="His upstream moved.", branch="skills-upstream/0123456789ab-abcdef12")
    hub.comment(REPO, pr, marks.ready_comment(None, hub.head(pr), hub.now))  # in the merge line

    def closed_skills(hub_, store_, repo, number, **kwargs):
        return FollowReport(number, 20, "closed_skills", "0123456789ab")

    monkeypatch.setattr(reconcile_module, "follow", closed_skills)
    done = sweep(hub, store, tmp_path)
    assert ("closed", f"#{pr}") in done.repairs
    assert [code for code, _ in done.repairs if code == "unparked"] == []
