"""#424 unit 5: a pull request with a ready mark and no auto-merge, into a base that requires none of the
required jobs (the mark-only class), is merged by `follow` once its required checks are green on its head;
a base that requires them is the merge line's, and `follow` never merges there."""

import json
from argparse import Namespace

from pathlib import Path

from fake_github import FAILED, PASSED, PENDING, REPO, REQUIRED
from follow_world import world

from orch_it.merge import commands
from orch_it.merge.follow import follow, prs_of_workflow_run
from orch_it.merge.settings import MergeSettings


def run(hub, store, pr, **kwargs):
    return follow(hub, store, REPO, pr, base="main", settings=MergeSettings(), now=hub.now, **kwargs)


def mark_only(tmp_path):
    """`pr open` on a base that requires nothing leaves the ready mark and no auto-merge."""

    hub, store, prs = world(tmp_path)
    hub.required_by_base = {}
    hub.set_auto_merge(REPO, prs[10], None)
    hub.calls.clear()
    return hub, store, prs[10]


def merges(hub):
    return [call for call in hub.calls if call[0] == "merge"]


def test_green_on_a_base_that_requires_nothing_is_merged_once_at_the_head_it_read(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    head = hub.head(pr)
    hub.checks_on(head, PASSED)
    done = run(hub, store, pr)
    assert done.merged_by_follow and done.outcome == "closed" and done.ticket == 10
    assert merges(hub) == [("merge", pr, MergeSettings().merge_method)]
    assert hub.pull(REPO, pr).state == "merged" and not hub.issues[(REPO, 10)].open  # its ticket closed in the same run
    assert run(hub, store, pr).merged_by_follow is False and len(merges(hub)) == 1  # a second follow merges nothing


def test_the_merge_names_the_head_sha_the_follow_read(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    head = hub.head(pr)
    hub.checks_on(head, PASSED)
    seen = []
    real = hub.merge
    hub.merge = lambda repo, number, sha, method: (seen.append(sha), real(repo, number, sha, method))[1]
    run(hub, store, pr)
    assert seen == [head]


def test_a_pending_check_is_not_merged(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.checks_on(hub.head(pr), PENDING)
    done = run(hub, store, pr)
    assert (done.outcome, done.merged_by_follow) == ("waiting", False) and not merges(hub)


def test_a_missing_check_is_not_merged(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.checks_on(hub.head(pr), PASSED, names=REQUIRED[:1])
    assert run(hub, store, pr).outcome == "waiting" and not merges(hub)


def test_red_goes_the_red_path_as_before_and_is_not_merged(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.checks_on(hub.head(pr), FAILED, failing=["t::x"])
    done = run(hub, store, pr)
    assert (done.outcome, done.merged_by_follow) == ("red", False) and not merges(hub)


def test_behind_is_not_merged_and_not_updated_by_the_follow(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.checks_on(hub.head(pr), PASSED)
    hub.land()
    done = run(hub, store, pr)
    assert (done.outcome, done.merged_by_follow) == ("behind", False)
    assert not merges(hub) and not [c for c in hub.calls if c[0] == "update_branch"]


def test_a_base_that_requires_both_jobs_is_never_merged_by_the_follow(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.required_by_base = {"main": set(REQUIRED)}
    hub.checks_on(hub.head(pr), PASSED)
    done = run(hub, store, pr)
    assert (done.outcome, done.merged_by_follow) == ("green", False) and not merges(hub)


def test_a_base_that_requires_only_one_job_is_mark_only(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.required_by_base = {"main": {REQUIRED[0]}}
    hub.checks_on(hub.head(pr), PASSED)
    assert run(hub, store, pr).merged_by_follow and len(merges(hub)) == 1


def test_auto_merge_already_on_is_not_merged_by_the_follow(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.set_auto_merge(REPO, pr, "merge")
    hub.checks_on(hub.head(pr), PASSED)
    done = run(hub, store, pr)
    assert (done.outcome, done.merged_by_follow) == ("green", False) and not merges(hub)


def test_a_draft_is_not_merged(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.pulls[pr].draft = True
    hub.checks_on(hub.head(pr), PASSED)
    assert run(hub, store, pr).outcome == "green" and not merges(hub)


def test_a_head_from_a_fork_is_not_merged(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.pulls[pr].head_repo = "fork-owner/other"
    hub.checks_on(hub.head(pr), PASSED)
    assert run(hub, store, pr).outcome == "green" and not merges(hub)


def test_the_merge_line_s_own_follow_never_merges(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.checks_on(hub.head(pr), PASSED)
    done = run(hub, store, pr, merge=False)
    assert (done.outcome, done.merged_by_follow) == ("green", False) and not merges(hub)


def test_a_closing_word_in_the_text_refuses_the_merge_and_leaves_the_line(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.pulls[pr].body = "Refs #10\nCloses #10\n"
    hub.checks_on(hub.head(pr), PASSED)
    done = run(hub, store, pr)
    assert (done.outcome, done.merged_by_follow) == ("refused", False) and not merges(hub)
    assert "closing_keyword" in hub.comments(REPO, pr)[-1].body and hub.issues[(REPO, 10)].open
    assert run(hub, store, pr).outcome == "idle" and not merges(hub)  # the left mark holds it out until a new ready mark


def test_a_merge_github_refuses_is_waiting_not_an_error(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.checks_on(hub.head(pr), PASSED)
    hub.merge_refusals = 1
    done = run(hub, store, pr)
    assert (done.outcome, done.merged_by_follow) == ("waiting", False)
    assert run(hub, store, pr).merged_by_follow  # the next follow reads again and merges


# the workflow_run event


def event(sha, number=None, branch=None):
    return {"workflow_run": {"head_sha": sha, "head_branch": branch,
                             "pull_requests": [{"number": number}] if number else []}}


def test_a_workflow_run_names_the_open_pull_request_with_its_head_sha_and_no_other(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    other = hub.open_pull(body="Refs #1", auto_merge=None)
    sha = hub.head(pr)
    assert prs_of_workflow_run(event(sha, pr), hub, REPO, "main") == ([pr], "")
    assert prs_of_workflow_run(event(hub.head(other), pr), hub, REPO, "main")[0] == []  # the pull request moved on
    assert prs_of_workflow_run(event(sha, None, hub.pulls[pr].branch), hub, REPO, "main") == ([pr], "")
    assert prs_of_workflow_run(event(sha), hub, REPO, "main") == ([pr], "")  # a head sha alone finds it too
    assert prs_of_workflow_run(event(hub.head(other)), hub, REPO, "main") == ([other], "")


def test_a_workflow_run_with_no_pull_request_names_none(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    found, why = prs_of_workflow_run(event("0" * 40, None, "a-push-branch"), hub, REPO, "main")
    assert found == [] and why
    assert prs_of_workflow_run({"workflow_run": "x"}, hub, REPO, "main")[0] == []
    assert prs_of_workflow_run({"workflow_run": {}}, hub, REPO, "main")[0] == []


def test_a_merged_pull_request_is_not_named_by_a_workflow_run(tmp_path):
    hub, store, pr = mark_only(tmp_path)
    sha = hub.head(pr)
    hub.checks_on(sha, PASSED)
    assert hub.merge(REPO, pr, sha, "merge")
    assert prs_of_workflow_run(event(sha, pr), hub, REPO, "main")[0] == []


def command(monkeypatch, tmp_path, hub, store, payload):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), "utf-8")
    monkeypatch.setattr(commands, "GhPullHub", lambda: hub)
    monkeypatch.setattr(commands.settings, "load", lambda: {})
    args = Namespace(group="follow", pr=None, event=str(path), base="main", role="github", repo=REPO)
    return commands._follow(args, lambda: store, lambda _: REPO, hub.now)


def test_the_follow_command_merges_the_pull_request_of_a_workflow_run(monkeypatch, tmp_path):
    hub, store, pr = mark_only(tmp_path)
    other = hub.open_pull(body="Refs #1")
    hub.checks_on(hub.head(pr), PASSED)
    hub.checks_on(hub.head(other), PASSED)
    said = command(monkeypatch, tmp_path, hub, store, event(hub.head(pr), pr))
    assert f"#{pr} closed ticket=#10" in said and "merged_by_follow" in said
    assert [call[1] for call in merges(hub)] == [pr]  # the other pull request has no mark and is no concern of the run


def test_the_follow_command_on_a_workflow_run_with_no_pull_request_is_a_no_op(monkeypatch, tmp_path):
    hub, store, pr = mark_only(tmp_path)
    hub.checks_on(hub.head(pr), PASSED)
    said = command(monkeypatch, tmp_path, hub, store, event("0" * 40, None, "a-push-branch"))
    assert said.startswith("skipped:") and not merges(hub)


def test_the_follow_workflow_listens_to_the_end_of_the_tests_run():
    text = (Path(__file__).parents[1] / ".github" / "workflows" / "follow.yml").read_text("utf-8")
    assert "  workflow_run:\n    workflows: [tests]\n    types: [completed]\n" in text
