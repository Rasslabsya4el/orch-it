"""`GhPullHub` (#400): the merge flow's view of GitHub through `gh`, here against a recorded `gh`
that answers as GitHub does and a recorded `git`; one test runs real git on local repos."""

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from orch_it import settings
from orch_it.merge import hub as hub_mod
from orch_it.merge.gh import GhPullHub
from orch_it.merge.git import GitDone
from orch_it.merge.hub import FAILED, NO_RESULT, PASSED, PENDING
from orch_it.testruns.github import GithubError
from required_names import LINUX_CHECK, WINDOWS_CHECK

REPO = "owner-x/repo-y"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


class Gh:
    """Answers `gh` calls by the first matching (words, answer) pair; an answer may be a function
    of the call. Records each call and the JSON of any `--input` file as it stood when called."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[list[str]] = []
        self.inputs: list[object] = []

    def __call__(self, args):
        self.calls.append(args)
        if "--input" in args:
            self.inputs.append(json.loads(Path(args[args.index("--input") + 1]).read_text("utf-8")))
        for words, answer in self.answers:
            if all(word in args for word in words):
                if callable(answer):
                    answer = answer(args)
                if isinstance(answer, Exception):
                    raise answer
                return answer if isinstance(answer, str) else json.dumps(answer)
        raise AssertionError(f"no answer for {args}")

    def called(self, word):
        return [c for c in self.calls if word in c]


def failed(status, text="x"):
    return GithubError("gh_failed", f"api repos/x/y: gh: {text} (HTTP {status})")


NOT_FOUND = failed(404, "Not Found")


@pytest.fixture(autouse=True)
def alone(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setattr(settings, "data_folder", lambda: tmp_path / "data")


def pull_json(number=5, **over):
    found = {
        "number": number, "title": "T", "body": "B", "state": "open", "draft": False,
        "created_at": "2026-11-02T10:00:00Z", "merged_at": None, "closed_at": None,
        "merge_commit_sha": None, "auto_merge": None, "mergeable": True,
        "head": {"ref": "feature", "sha": "h" * 7}, "base": {"ref": "main", "sha": "b" * 7}}
    return found | over


def pull_url(number=5):
    return f"repos/{REPO}/pulls/{number}"


def compare_url(base="main", head="h" * 7):
    return f"repos/{REPO}/compare/{base}...{head}?per_page=1"


# --- pull requests ----------------------------------------------------------------------------


def test_an_open_pull_request_is_read_with_its_mergeable_and_behind_and_an_auto_merge_it_carries_is_ignored():
    gh = Gh(([pull_url()], pull_json(auto_merge={"merge_method": "SQUASH"}, draft=True)),
            ([compare_url()], {"behind_by": 3, "ahead_by": 1}))
    found = GhPullHub(gh).pull(REPO, 5)
    assert found == hub_mod.Pull(
        repo=REPO, number=5, title="T", body="B", branch="feature", head_sha="h" * 7, base="main",
        state="open", draft=True, mergeable="mergeable", behind=3,
        created_at=datetime(2026, 11, 2, 10, tzinfo=UTC))


def test_the_head_repo_tells_a_fork_from_the_repo_itself():
    gh = Gh(([pull_url(5)], pull_json(5, head={"ref": "x", "sha": "h" * 7, "repo": {"full_name": REPO}})),
            ([pull_url(6)], pull_json(6, head={"ref": "x", "sha": "h" * 7, "repo": {"full_name": "stranger/fork"}})),
            ([pull_url(7)], pull_json(7, head={"ref": "x", "sha": "h" * 7, "repo": None})),
            ([compare_url()], {"behind_by": 0}))
    hub = GhPullHub(gh)
    assert [hub.pull(REPO, n).head_repo for n in (5, 6, 7)] == [REPO, "stranger/fork", None]


def test_a_pull_request_that_merged_between_the_list_and_its_read_is_not_listed_open():
    gh = Gh((["--paginate", f"repos/{REPO}/pulls?state=open&base=main&per_page=100"], [{"number": 5}, {"number": 6}]),
            ([pull_url(5)], pull_json(5)), ([pull_url(6)], pull_json(6, state="closed", merged_at="2026-11-03T10:00:00Z")),
            ([compare_url()], {"behind_by": 0}))
    assert [p.number for p in GhPullHub(gh).open_pulls(REPO, "main")] == [5]


def test_the_events_artifacts_are_the_layer_s_own_unexpired_ones_from_the_newest_pages():
    page = [{"name": "orch-it-events-follow-7-1", "expired": False, "workflow_run": {"id": 7}},
            {"name": artifact(LINUX_CHECK, 1), "expired": False, "workflow_run": {"id": 7}},
            {"name": "orch-it-events-follow-6-1", "expired": True, "workflow_run": {"id": 6}},
            {"name": "orch-it-events-main-5-1", "expired": False, "workflow_run": None}]
    gh = Gh(([f"repos/{REPO}/actions/artifacts?per_page=100&page=1"], {"artifacts": page}))
    assert GhPullHub(gh).event_artifacts(REPO) == [("7", "orch-it-events-follow-7-1")]
    assert len(gh.calls) == 1  # a short page is the last


def test_an_artifact_is_unpacked_with_gh_run_download(tmp_path):
    gh = Gh((["run", "download"], ""))
    GhPullHub(gh).download_artifact(REPO, "7", "orch-it-events-follow-7-1", tmp_path)
    assert gh.calls == [["run", "download", "7", "--repo", REPO, "-n", "orch-it-events-follow-7-1", "-D", str(tmp_path)]]


def test_a_pull_request_is_closed_by_its_state():
    gh = Gh(([f"repos/{REPO}/pulls/5", "PATCH"], {}))
    GhPullHub(gh).close_pull(REPO, 5)
    assert gh.inputs == [{"state": "closed"}]


def test_a_merged_and_a_closed_pull_request_carry_their_end_and_are_not_compared():
    gh = Gh(([pull_url(5)], pull_json(5, state="closed", merged_at="2026-11-03T10:00:00Z",
                                      closed_at="2026-11-03T10:00:00Z", merge_commit_sha="m" * 7)),
            ([pull_url(6)], pull_json(6, state="closed", closed_at="2026-11-04T10:00:00Z",
                                      merge_commit_sha="x" * 7)))
    hub = GhPullHub(gh)
    merged, closed = hub.pull(REPO, 5), hub.pull(REPO, 6)
    assert (merged.state, merged.merge_sha) == ("merged", "m" * 7)
    assert merged.merged_at == datetime(2026, 11, 3, 10, tzinfo=UTC)
    assert (closed.state, closed.merge_sha, closed.merged_at, closed.behind) == ("closed", None, None, None)
    assert closed.closed_at == datetime(2026, 11, 4, 10, tzinfo=UTC)
    assert not gh.called("repos/owner-x/repo-y/compare/main...hhhhhhh?per_page=1")


def test_a_pull_request_reads_open_while_merged_at_is_null_after_a_base_advance_then_merged():
    answers = iter([pull_json(5), pull_json(5, state="closed", merged_at="2026-11-03T10:00:00Z")])
    gh = Gh(([pull_url(5)], lambda args: next(answers)), ([compare_url()], {"behind_by": 0}))
    hub = GhPullHub(gh)
    assert [hub.pull(REPO, 5).state, hub.pull(REPO, 5).state] == ["open", "merged"]


@pytest.mark.parametrize(("mergeable", "word"), [(True, "mergeable"), (False, "conflicting"), (None, "unknown")])
def test_mergeable_is_true_false_or_not_computed_yet(mergeable, word):
    gh = Gh(([pull_url()], pull_json(mergeable=mergeable)), ([compare_url()], {"behind_by": 0}))
    assert GhPullHub(gh).pull(REPO, 5).mergeable == word


def test_a_field_the_list_api_lacks_reads_as_not_computed_and_a_compare_that_is_not_there_as_unknown():
    item = pull_json()
    del item["mergeable"]
    gh = Gh(([pull_url()], item), ([compare_url()], NOT_FOUND))
    found = GhPullHub(gh).pull(REPO, 5)
    assert (found.mergeable, found.behind) == ("unknown", None)


def test_a_missing_pull_request_is_none_and_a_failure_raises():
    gh = Gh(([pull_url(1)], NOT_FOUND), ([pull_url(2)], failed(500, "Server Error")))
    hub = GhPullHub(gh)
    assert hub.pull(REPO, 1) is None
    with pytest.raises(GithubError) as error:
        hub.pull(REPO, 2)
    assert error.value.code == "gh_failed"


@pytest.mark.parametrize("answer", ["", "not json", "[1]", "{}", json.dumps(pull_json(head=None)),
                                    json.dumps(pull_json(created_at="soon"))])
def test_a_pull_request_answer_not_of_the_shape_asked_for_is_bad_output(answer):
    gh = Gh(([pull_url()], answer), ([compare_url()], {"behind_by": 0}))
    with pytest.raises(GithubError) as error:
        GhPullHub(gh).pull(REPO, 5)
    assert error.value.code == "gh_bad_output"


def test_a_compare_without_behind_by_is_bad_output():
    gh = Gh(([pull_url()], pull_json()), ([compare_url()], {"ahead_by": 1}))
    with pytest.raises(GithubError) as error:
        GhPullHub(gh).pull(REPO, 5)
    assert error.value.code == "gh_bad_output"


def test_open_pulls_come_from_every_page_each_read_again_oldest_first():
    pages = json.dumps([{"number": 7}, {"number": 5}]) + json.dumps([{"number": 6}])
    gh = Gh(([f"repos/{REPO}/pulls?state=open&base=release%2F1&per_page=100", "--paginate"], pages),
            ([pull_url(5)], pull_json(5, created_at="2026-11-02T10:00:00Z", mergeable=None,
                                      head={"ref": "a", "sha": "a1"}, base={"ref": "release/1", "sha": "b"})),
            ([pull_url(6)], pull_json(6, created_at="2026-11-01T10:00:00Z", draft=True,
                                      head={"ref": "b", "sha": "b1"}, base={"ref": "release/1", "sha": "b"})),
            ([pull_url(7)], NOT_FOUND),
            ([f"repos/{REPO}/compare/release%2F1...a1?per_page=1"], {"behind_by": 2}),
            ([f"repos/{REPO}/compare/release%2F1...b1?per_page=1"], {"behind_by": 0}))
    found = GhPullHub(gh).open_pulls(REPO, "release/1")
    assert [(p.number, p.draft, p.mergeable, p.behind) for p in found] == [
        (6, True, "mergeable", 0), (5, False, "unknown", 2)]


def test_open_pulls_of_a_list_with_a_pull_request_without_a_number_is_bad_output():
    gh = Gh(([f"repos/{REPO}/pulls?state=open&base=main&per_page=100"], [{"title": "x"}]))
    with pytest.raises(GithubError) as error:
        GhPullHub(gh).open_pulls(REPO, "main")
    assert error.value.code == "gh_bad_output"


# --- check runs -------------------------------------------------------------------------------


def run_of(id, name, status="completed", conclusion="success", run=None, started="2026-11-02T10:00:00Z"):
    url = f"https://github.com/{REPO}/actions/runs/{run}/job/{id}" if run else None
    return {"id": id, "name": name, "status": status, "conclusion": conclusion, "details_url": url,
            "started_at": started}


CHECKS = f"repos/{REPO}/commits/{'c' * 7}/check-runs?per_page=100"
STATUS = f"repos/{REPO}/commits/{'c' * 7}/status?per_page=100"


@pytest.mark.parametrize(("status", "conclusion", "state"), [
    ("completed", "success", PASSED), ("completed", "failure", FAILED),
    ("queued", None, PENDING), ("in_progress", None, PENDING), ("waiting", None, PENDING),
    ("completed", "cancelled", NO_RESULT), ("completed", "timed_out", NO_RESULT),
    ("completed", "skipped", NO_RESULT), ("completed", "stale", NO_RESULT),
    ("completed", "neutral", NO_RESULT), ("completed", "action_required", NO_RESULT),
    ("completed", "startup_failure", NO_RESULT), ("completed", None, NO_RESULT)])
def test_every_conclusion_of_a_check_run_maps_to_one_state(status, conclusion, state):
    gh = Gh(([CHECKS], {"check_runs": [run_of(1, LINUX_CHECK, status, conclusion)]}))
    [check] = GhPullHub(gh).checks(REPO, "c" * 7)
    assert (check.name, check.state, check.run_id, check.attempt) == (LINUX_CHECK, state, None, 1)


def test_the_latest_run_of_each_name_counts_across_pages_with_its_run_and_attempt_read_once():
    pages = (json.dumps({"total_count": 3, "check_runs": [
        run_of(10, LINUX_CHECK, "completed", "failure", run=500), run_of(30, WINDOWS_CHECK, run=500)]})
        + json.dumps({"total_count": 3, "check_runs": [
            run_of(20, LINUX_CHECK, "completed", "success", run=501, started="2026-11-02T11:00:00Z"),
            run_of(5, "lint", "in_progress", None)]}))
    gh = Gh(([CHECKS, "--paginate"], pages),
            ([f"repos/{REPO}/actions/runs/500"], {"run_attempt": 2}),
            ([f"repos/{REPO}/actions/runs/501"], {"run_attempt": 3}))
    found = {c.name: c for c in GhPullHub(gh).checks(REPO, "c" * 7)}
    assert {n: (c.state, c.run_id, c.attempt) for n, c in found.items()} == {
        LINUX_CHECK: (PASSED, "501", 3), WINDOWS_CHECK: (PASSED, "500", 2), "lint": (PENDING, None, 1)}
    assert found[LINUX_CHECK].started_at == datetime(2026, 11, 2, 11, tzinfo=UTC)
    assert len(gh.called(f"repos/{REPO}/actions/runs/500")) == 1


def test_two_checks_of_one_run_read_the_run_once():
    gh = Gh(([CHECKS], {"check_runs": [run_of(1, LINUX_CHECK, run=9), run_of(2, WINDOWS_CHECK, run=9)]}),
            ([f"repos/{REPO}/actions/runs/9"], {"run_attempt": 4}))
    assert [(c.name, c.attempt) for c in GhPullHub(gh).checks(REPO, "c" * 7)] == [(LINUX_CHECK, 4), (WINDOWS_CHECK, 4)]
    assert len(gh.called(f"repos/{REPO}/actions/runs/9")) == 1


def test_a_run_that_is_gone_or_has_no_attempt_reads_as_the_first():
    gh = Gh(([CHECKS], {"check_runs": [run_of(1, LINUX_CHECK, run=9), run_of(2, WINDOWS_CHECK, run=8)]}),
            ([f"repos/{REPO}/actions/runs/9"], NOT_FOUND),
            ([f"repos/{REPO}/actions/runs/8"], {"status": "completed"}))
    assert [c.attempt for c in GhPullHub(gh).checks(REPO, "c" * 7)] == [1, 1]


@pytest.mark.parametrize("answer", ["", "nope", "[]", '{"runs": []}', '{"check_runs": [{"name": "x"}]}',
                                    '{"check_runs": [{"id": 1}]}', '{"check_runs": 3}'])
def test_check_runs_not_of_the_shape_asked_for_are_bad_output(answer):
    with pytest.raises(GithubError) as error:
        GhPullHub(Gh(([CHECKS], answer))).checks(REPO, "c" * 7)
    assert error.value.code == "gh_bad_output"


def test_a_sha_with_no_check_runs_has_no_checks_and_a_failing_call_raises():
    assert GhPullHub(Gh(([CHECKS], {"total_count": 0, "check_runs": []}))).checks(REPO, "c" * 7) == []
    with pytest.raises(GithubError):
        GhPullHub(Gh(([CHECKS], failed(500)))).checks(REPO, "c" * 7)


def status_of(context, state):
    return {"context": context, "state": state, "created_at": "2026-11-02T10:30:00Z"}


@pytest.mark.parametrize(("state", "expected"), [("success", PASSED), ("failure", FAILED), ("error", FAILED),
                                                 ("pending", PENDING), ("odd", NO_RESULT)])
def test_a_commit_status_is_a_check_under_its_context_and_a_check_run_wins_over_a_status_of_its_name(state, expected):
    gh = Gh(([CHECKS], {"check_runs": [run_of(1, LINUX_CHECK, "completed", "failure")]}),
            ([STATUS], {"statuses": [status_of(WINDOWS_CHECK, state),
                                                         status_of(LINUX_CHECK, "success")]}))
    found = {c.name: c for c in GhPullHub(gh).checks(REPO, "c" * 7, statuses=True)}
    assert {n: c.state for n, c in found.items()} == {LINUX_CHECK: FAILED, WINDOWS_CHECK: expected}
    assert found[WINDOWS_CHECK].run_id is None and found[WINDOWS_CHECK].started_at == datetime(
        2026, 11, 2, 10, 30, tzinfo=UTC)


def test_the_status_read_is_asked_for_and_the_default_read_makes_the_calls_it_made_before():
    runs = ([CHECKS], {"check_runs": [run_of(1, LINUX_CHECK, run=9)]})
    attempt = ([f"repos/{REPO}/actions/runs/9"], {"run_attempt": 1})
    plain = Gh(runs, attempt)  # no route for the statuses: a default read that asked for them would fail
    assert [c.name for c in GhPullHub(plain).checks(REPO, "c" * 7)] == [LINUX_CHECK]
    assert len(plain.calls) == 2 and not plain.called(STATUS)
    asked = Gh(runs, attempt, ([STATUS], {"statuses": []}))
    assert [c.name for c in GhPullHub(asked).checks(REPO, "c" * 7, statuses=True)] == [LINUX_CHECK]
    assert len(asked.calls) == 3 and len(asked.called(STATUS)) == 1


def test_post_status_posts_a_commit_status_not_a_check_run_with_the_state_words_of_github():
    gh = Gh(([f"repos/{REPO}/statuses/{'c' * 7}"], {"id": 1}))
    GhPullHub(gh).post_status(REPO, "c" * 7, LINUX_CHECK, PASSED, "d" * 200)
    [call] = gh.calls
    assert call[:4] == ["api", "-X", "POST", f"repos/{REPO}/statuses/{'c' * 7}"]
    assert gh.inputs == [{"state": "success", "context": LINUX_CHECK, "description": "d" * 140}]


# --- pull comments ------------------------------------------------------------------------------------


def test_pull_comments_of_three_sources_are_merged_in_time_order_and_a_review_without_a_body_is_skipped():
    gh = Gh(([f"repos/{REPO}/issues/5/comments?per_page=100"], [
        {"user": {"login": "ann", "type": "User"}, "body": "comment", "created_at": "2026-11-02T12:00:00Z"},
        {"user": {"login": "ci[bot]", "type": "Bot"}, "body": "bot says", "created_at": "2026-11-02T09:00:00Z"}]),
        ([f"repos/{REPO}/pulls/5/reviews?per_page=100"], [
            {"user": {"login": "bob", "type": "User"}, "body": "looks off", "state": "COMMENTED",
             "submitted_at": "2026-11-02T10:00:00Z"},
            {"user": {"login": "bob", "type": "User"}, "body": "", "state": "APPROVED",
             "submitted_at": "2026-11-02T10:30:00Z"},
            {"user": {"login": "cy", "type": "User"}, "body": "", "state": "CHANGES_REQUESTED",
             "submitted_at": "2026-11-02T13:00:00Z"},
            {"user": {"login": "dee", "type": "User"}, "body": "draft", "state": "PENDING", "submitted_at": None}]),
        ([f"repos/{REPO}/pulls/5/comments?per_page=100"], [
            {"user": {"login": "bob", "type": "User"}, "body": "on a line", "created_at": "2026-11-02T11:00:00Z"},
            {"user": None, "body": "ghost line", "created_at": "2026-11-02T14:00:00Z"}]))
    said = GhPullHub(gh).pull_comments(REPO, 5)
    assert [(c.author, c.bot, c.body) for c in said] == [
        ("ci[bot]", True, "bot says"), ("bob", False, "looks off"), ("bob", False, "on a line"),
        ("ann", False, "comment"), ("cy", False, ""), ("ghost", False, "ghost line")]
    assert said[0].at == datetime(2026, 11, 2, 9, tzinfo=UTC)


def test_pull_comments_are_read_from_every_page():
    gh = Gh(([f"repos/{REPO}/issues/5/comments?per_page=100", "--paginate"],
             json.dumps([{"user": {"login": "a", "type": "User"}, "body": "1", "created_at": "2026-11-02T10:00:00Z"}])
             + json.dumps([{"user": {"login": "a", "type": "User"}, "body": "2",
                            "created_at": "2026-11-02T11:00:00Z"}])),
            ([f"repos/{REPO}/pulls/5/reviews?per_page=100", "--paginate"],
             json.dumps([]) + json.dumps([{"user": {"login": "b", "type": "User"}, "body": "3", "state": "COMMENTED",
                                           "submitted_at": "2026-11-02T10:30:00Z"}])),
            ([f"repos/{REPO}/pulls/5/comments?per_page=100", "--paginate"], []))
    assert [c.body for c in GhPullHub(gh).pull_comments(REPO, 5)] == ["1", "3", "2"]


def test_pull_comments_with_a_source_not_a_list_are_bad_output():
    gh = Gh(([f"repos/{REPO}/issues/5/comments?per_page=100"], []),
            ([f"repos/{REPO}/pulls/5/reviews?per_page=100"], {"message": "x"}))
    with pytest.raises(GithubError) as error:
        GhPullHub(gh).pull_comments(REPO, 5)
    assert error.value.code == "gh_bad_output"


# --- writing pull requests --------------------------------------------------------------------


def test_a_pull_request_is_created_from_a_body_file_and_its_number_read_from_the_url():
    seen = {}

    def create(args):
        path = Path(args[args.index("--body-file") + 1])
        seen["body"] = path.read_text("utf-8")
        seen["path"] = path
        return f"Creating pull request for feature into main in {REPO}\n\nhttps://github.com/{REPO}/pull/42\n"

    gh = Gh((["pr", "create"], create))
    number = GhPullHub(gh).create_pull(REPO, "main", "feature", "A title", "long body " * 5000)
    assert number == 42
    assert gh.calls[0][:11] == ["pr", "create", "--repo", REPO, "--base", "main", "--head", "feature", "--title",
                                "A title", "--body-file"]
    assert seen["body"] == "long body " * 5000
    assert not seen["path"].exists() and not seen["path"].parent.exists()


@pytest.mark.parametrize("printed", ["", "done\n", "https://github.com/o/r/issues/4\n"])
def test_a_create_that_prints_no_pull_request_url_is_bad_output(printed):
    with pytest.raises(GithubError) as error:
        GhPullHub(Gh((["pr", "create"], printed))).create_pull(REPO, "main", "feature", "t", "b")
    assert error.value.code == "gh_bad_output"


def test_a_failed_create_raises_and_leaves_no_temp_file():
    seen = {}

    def create(args):
        seen["path"] = Path(args[args.index("--body-file") + 1])
        return GithubError("gh_failed", "pr create: a pull request already exists")

    with pytest.raises(GithubError) as error:
        GhPullHub(Gh((["pr", "create"], create))).create_pull(REPO, "main", "feature", "t", "b")
    assert error.value.code == "gh_failed"
    assert not seen["path"].parent.exists()


# --- the checks a base requires (#424 unit 4) -----------------------------------------------------

BRANCH = f"repos/{REPO}/branches/main"  # the route a read-only token may call; the /protection route needs admin
RULES = f"repos/{REPO}/rules/branches/main"
FIRST, SECOND = "job-one", "job-two"  # invented names: the required jobs are a setting, never a literal here


def classic(*contexts, checks=()):
    """What `GET branches/<base>` answers for a protected branch."""
    return {"name": "main", "protected": True,
            "protection": {"enabled": True, "required_status_checks": {
                "enforcement_level": "everyone", "contexts": list(contexts),
                "checks": [{"context": name, "app_id": None} for name in checks]}}}


def rules(*contexts):
    return [{"type": "pull_request", "parameters": {}},
            {"type": "required_status_checks",
             "parameters": {"required_status_checks": [{"context": name, "integration_id": None}
                                                       for name in contexts]}}]


def test_classic_protection_names_the_jobs_in_contexts_and_in_checks():
    gh = Gh(([BRANCH], classic(FIRST, checks=[SECOND])), ([RULES], []))
    assert GhPullHub(gh).required_checks_of(REPO, "main") == {FIRST, SECOND}


def test_rulesets_name_the_jobs_of_their_required_status_checks_rule():
    gh = Gh(([BRANCH], NOT_FOUND), ([RULES], rules(FIRST, SECOND)))
    assert GhPullHub(gh).required_checks_of(REPO, "main") == {FIRST, SECOND}


def test_a_base_that_requires_only_one_of_two_jobs_names_only_that_one():
    gh = Gh(([BRANCH], classic(FIRST)), ([RULES], rules()))
    assert GhPullHub(gh).required_checks_of(REPO, "main") == {FIRST}


@pytest.mark.parametrize("error", [failed(403, "Upgrade to GitHub Pro or make this repository public"),
                                   failed(403, "Branch not protected"), NOT_FOUND])
def test_a_plan_or_protection_refusal_is_a_base_that_requires_nothing(error):
    gh = Gh(([BRANCH], error), ([RULES], error))
    assert GhPullHub(gh).required_checks_of(REPO, "main") == set()


@pytest.mark.parametrize("text", ["API rate limit exceeded for user ID 1",
                                  "You have triggered an abuse detection mechanism",
                                  "Resource not accessible by integration"])
def test_a_403_that_is_not_the_plan_or_protection_refusal_raises(text):
    refused = failed(403, text)
    for gh in (Gh(([BRANCH], refused), ([RULES], [])), Gh(([BRANCH], NOT_FOUND), ([RULES], refused))):
        with pytest.raises(GithubError) as error:
            GhPullHub(gh).required_checks_of(REPO, "main")
        assert error.value.code == "gh_failed"


def test_an_unprotected_branch_with_the_empty_contexts_object_requires_nothing():
    unprotected = {"name": "main", "protected": False,
                   "protection": {"enabled": False, "required_status_checks": {
                       "enforcement_level": "off", "contexts": [], "checks": []}}}
    gh = Gh(([BRANCH], unprotected), ([RULES], []))
    assert GhPullHub(gh).required_checks_of(REPO, "main") == set()


@pytest.mark.parametrize("answer", [{"name": "main", "protected": False},
                                    {"name": "main", "protected": False, "protection": {
                                        "required_status_checks": {"contexts": [FIRST], "checks": []}}},
                                    {"name": "main", "protection": None},
                                    {"name": "main", "protected": True, "protection": {"enabled": True}},
                                    {"name": "main", "protection": {"required_status_checks": None}}])
def test_an_answer_with_no_protection_or_no_required_checks_requires_nothing(answer):
    gh = Gh(([BRANCH], answer), ([RULES], []))
    assert GhPullHub(gh).required_checks_of(REPO, "main") == set()


def test_a_base_with_a_slash_is_encoded_in_both_routes():
    base = "release/1"
    gh = Gh(([f"repos/{REPO}/branches/release%2F1"], classic(FIRST)), ([f"repos/{REPO}/rules/branches/release%2F1"], []))
    assert GhPullHub(gh).required_checks_of(REPO, base) == {FIRST}


def test_the_admin_only_protection_route_is_never_called():
    gh = Gh(([BRANCH], classic(FIRST)), ([RULES], []))
    GhPullHub(gh).required_checks_of(REPO, "main")
    assert not [c for c in gh.calls if any(a.endswith("/protection") for a in c)]


def test_empty_lists_are_a_base_that_requires_nothing():
    gh = Gh(([BRANCH], classic()), ([RULES], []))
    assert GhPullHub(gh).required_checks_of(REPO, "main") == set()


def test_any_other_failure_of_the_read_raises():
    gh = Gh(([BRANCH], failed(500, "boom")), ([RULES], []))
    with pytest.raises(GithubError) as error:
        GhPullHub(gh).required_checks_of(REPO, "main")
    assert error.value.code == "gh_failed"


def test_the_branch_is_updated_only_from_the_head_the_caller_saw():
    gh = Gh((["PUT", f"repos/{REPO}/pulls/5/update-branch"], {"message": "Updating pull request branch."}))
    assert GhPullHub(gh).update_branch(REPO, 5, "abc") is True
    assert gh.inputs == [{"expected_head_sha": "abc"}]


def test_an_update_that_conflicts_is_false_and_any_other_failure_raises():
    gh = Gh((["PUT", f"repos/{REPO}/pulls/5/update-branch"], failed(422, "merge conflict between base and head")),
            (["PUT", f"repos/{REPO}/pulls/6/update-branch"], failed(422, "expected head sha didn't match")),
            (["PUT", f"repos/{REPO}/pulls/7/update-branch"], failed(500, "merge conflict")))
    hub = GhPullHub(gh)
    assert hub.update_branch(REPO, 5, "abc") is False
    for number in (6, 7):
        with pytest.raises(GithubError):
            hub.update_branch(REPO, number, "abc")


def test_a_merge_is_pinned_to_the_head_and_returns_the_merge_commit():
    gh = Gh((["PUT", f"repos/{REPO}/pulls/5/merge"], {"sha": "m" * 7, "merged": True, "message": "ok"}))
    assert GhPullHub(gh).merge(REPO, 5, "abc", "squash") == "m" * 7
    assert gh.inputs == [{"sha": "abc", "merge_method": "squash"}]


@pytest.mark.parametrize("status", [405, 409, 422])
def test_a_merge_github_refuses_is_none(status):
    gh = Gh((["PUT"], failed(status, "Head branch was modified. Review and try the merge again.")))
    assert GhPullHub(gh).merge(REPO, 5, "abc", "merge") is None


@pytest.mark.parametrize("answer", [failed(500), failed(404, "Not Found"), GithubError("gh_timeout", "api"),
                                    "", "ok", '{"merged": true}'])
def test_a_merge_that_broke_or_answered_badly_raises(answer):
    with pytest.raises(GithubError):
        GhPullHub(Gh((["PUT"], answer))).merge(REPO, 5, "abc", "merge")


def advance_gh(error):
    return Gh((["PATCH", f"repos/{REPO}/git/refs/heads/main"], error))


@pytest.mark.parametrize("error", [failed(422, "Update is not a fast forward"), failed(409, "Update is not a fast forward"),
                                   failed(409, "Reference cannot be updated"), failed(409, "Conflict")])
def test_a_base_that_moved_is_not_advanced_and_is_false(error):
    assert GhPullHub(advance_gh(error)).advance_base(REPO, "main", "abc") is False


@pytest.mark.parametrize(("error", "reason"), [
    (failed(422, f"Required status check \"{LINUX_CHECK}\" is expected."),
     f"Required status check \"{LINUX_CHECK}\" is expected."),
    (failed(403, "Protected branch rules not configured for this branch"),
     "Protected branch rules not configured for this branch"),
    (failed(422, "Changes must be made through a pull request."), "Changes must be made through a pull request.")])
def test_a_protection_refusal_of_the_base_update_raises_landing_refused_with_its_reason(error, reason):
    with pytest.raises(hub_mod.LandingRefused) as refused:
        GhPullHub(advance_gh(error)).advance_base(REPO, "main", "abc")
    assert refused.value.reason == reason


@pytest.mark.parametrize("error", [failed(500, "boom"), failed(404, "Not Found"), GithubError("gh_timeout", "api")])
def test_any_other_failure_of_the_base_update_raises_github_error(error):
    with pytest.raises(GithubError):
        GhPullHub(advance_gh(error)).advance_base(REPO, "main", "abc")


def test_the_batch_branches_are_the_refs_under_the_batch_prefix_by_name():
    gh = Gh(([f"repos/{REPO}/git/matching-refs/heads/orch-batch/"],
             [{"ref": "refs/heads/orch-batch/1767225600-1"}, {"ref": "refs/heads/orch-batch/1767225700-2"}]))
    assert GhPullHub(gh).batch_branches(REPO) == ["orch-batch/1767225600-1", "orch-batch/1767225700-2"]
    assert "--paginate" in gh.calls[0]


def test_the_base_is_advanced_without_force():
    gh = advance_gh({"ref": "refs/heads/main"})
    assert GhPullHub(gh).advance_base(REPO, "main", "abc") is True
    assert gh.inputs == [{"sha": "abc", "force": False}]


# --- Actions ----------------------------------------------------------------------------------


def test_the_failed_jobs_of_a_run_are_run_again_and_a_run_is_cancelled():
    gh = Gh(([f"repos/{REPO}/actions/runs/9/rerun-failed-jobs"], ""), ([f"repos/{REPO}/actions/runs/9/cancel"], ""))
    hub = GhPullHub(gh)
    hub.rerun(REPO, "9")
    hub.cancel(REPO, "9")
    assert gh.calls == [["api", "-X", "POST", f"repos/{REPO}/actions/runs/9/rerun-failed-jobs"],
                        ["api", "-X", "POST", f"repos/{REPO}/actions/runs/9/cancel"]]


def test_cancelling_a_completed_run_is_no_error_and_a_failed_rerun_or_cancel_raises():
    gh = Gh(([f"repos/{REPO}/actions/runs/9/cancel"], failed(409, "Cannot cancel a workflow run that is completed.")),
            ([f"repos/{REPO}/actions/runs/8/cancel"], failed(403)),
            ([f"repos/{REPO}/actions/runs/9/rerun-failed-jobs"], failed(403, "Workflow is still in progress")))
    hub = GhPullHub(gh)
    hub.cancel(REPO, "9")
    with pytest.raises(GithubError):
        hub.cancel(REPO, "8")
    with pytest.raises(GithubError):
        hub.rerun(REPO, "9")


JUNIT = ('<testsuites><testsuite name="s">'
         '<testcase classname="tests.t_a" name="test_ok"/>'
         '<testcase classname="tests.t_a" name="test_red"><failure message="x"/></testcase>'
         '<testcase classname="tests.t_b" name="test_boom"><error message="y"/></testcase>'
         '<testcase classname="tests.t_b" name="test_skip"><skipped/></testcase>'
         '</testsuite></testsuites>')
JUNIT_OTHER = ('<testsuites><testsuite name="s"><testcase classname="tests.t_c" name="test_win">'
               '<failure message="z"/></testcase><testcase classname="tests.t_a" name="test_red">'
               '<failure message="x"/></testcase></testsuite></testsuites>')
ARTIFACTS = f"repos/{REPO}/actions/runs/9/artifacts?per_page=100"


def artifact(job, attempt):
    """The name of a job\'s JUnit artifact, as the workflow uploads it."""

    return f"junit-{job}-{attempt}"


def downloads(files):
    """A `gh run download` that writes `files` (artifact name -> junit text) where it is told."""

    def download(args):
        folder = Path(args[args.index("-D") + 1])
        (folder / "inner").mkdir()
        (folder / "inner" / "junit.xml").write_text(files[args[args.index("-n") + 1]], "utf-8")
        return ""

    return (["run", "download"], download)


def test_the_failed_and_errored_tests_of_the_junit_artifacts_are_listed_sorted_and_unique():
    gh = Gh(([ARTIFACTS], {"artifacts": [{"name": artifact(LINUX_CHECK, 1)}, {"name": artifact(WINDOWS_CHECK, 1)},
                                         {"name": "coverage"}]}),
            downloads({artifact(LINUX_CHECK, 1): JUNIT, artifact(WINDOWS_CHECK, 1): JUNIT_OTHER}))
    found = GhPullHub(gh).failing_ids(REPO, "9")
    assert found == ["tests.t_a::test_red", "tests.t_b::test_boom", "tests.t_c::test_win"]
    assert [c[:6] for c in gh.called("download")] == [
        ["run", "download", "9", "--repo", REPO, "-n"], ["run", "download", "9", "--repo", REPO, "-n"]]
    assert [c[c.index("-n") + 1] for c in gh.called("download")] == [artifact(LINUX_CHECK, 1), artifact(WINDOWS_CHECK, 1)]
    assert not Path(gh.called("download")[0][-1]).exists()  # the temp dir is gone


def test_the_results_of_a_run_name_the_job_of_each_report_and_whether_the_case_is_marked():
    marked = ('<testsuites><testsuite name="s"><testcase classname="tests.t_w" name="test_path"><properties>'
              '<property name="os_touching" value="1"/></properties><failure message="x"/></testcase>'
              '<testcase classname="tests.t_w" name="test_plain"/></testsuite></testsuites>')
    gh = Gh(([ARTIFACTS], {"artifacts": [{"name": artifact(LINUX_CHECK, 1)}, {"name": artifact(WINDOWS_CHECK, 1)}]}),
            downloads({artifact(LINUX_CHECK, 1): JUNIT_OTHER, artifact(WINDOWS_CHECK, 1): marked}))
    found = GhPullHub(gh).results(REPO, "9")
    assert [(r.job, r.test_id, r.outcome, r.marked) for r in found] == [
        (LINUX_CHECK, "tests.t_c::test_win", "failed", False), (LINUX_CHECK, "tests.t_a::test_red", "failed", False),
        (WINDOWS_CHECK, "tests.t_w::test_path", "failed", True), (WINDOWS_CHECK, "tests.t_w::test_plain", "passed", False)]
    assert GhPullHub(Gh(([ARTIFACTS], {"artifacts": []}))).results(REPO, "9") is None


def test_only_the_latest_attempt_of_a_job_is_read_and_an_expired_artifact_is_not():
    gh = Gh(([ARTIFACTS, "--paginate"],
             json.dumps({"artifacts": [{"name": artifact(LINUX_CHECK, 1)}, {"name": artifact(WINDOWS_CHECK, 1), "expired": True}]})
             + json.dumps({"artifacts": [{"name": artifact(LINUX_CHECK, 2)}]})),
            downloads({artifact(LINUX_CHECK, 1): JUNIT, artifact(LINUX_CHECK, 2): JUNIT_OTHER}))
    assert GhPullHub(gh).failing_ids(REPO, "9") == ["tests.t_a::test_red", "tests.t_c::test_win"]
    assert [c[c.index("-n") + 1] for c in gh.called("download")] == [artifact(LINUX_CHECK, 2)]


def test_a_green_report_is_an_empty_list_not_none():
    gh = Gh(([ARTIFACTS], {"artifacts": [{"name": artifact(LINUX_CHECK, 1)}]}),
            downloads({artifact(LINUX_CHECK, 1): '<testsuite><testcase classname="a" name="b"/></testsuite>'}))
    assert GhPullHub(gh).failing_ids(REPO, "9") == []


def test_no_junit_artifact_or_no_readable_report_is_none():
    assert GhPullHub(Gh(([ARTIFACTS], {"artifacts": [{"name": "coverage"}]}))).failing_ids(REPO, "9") is None
    assert GhPullHub(Gh(([ARTIFACTS], {"artifacts": []}))).failing_ids(REPO, "9") is None
    assert GhPullHub(Gh(([ARTIFACTS], {"artifacts": [{"name": artifact(LINUX_CHECK, 1), "expired": True}]}))
                     ).failing_ids(REPO, "9") is None
    assert GhPullHub(Gh(([f"repos/{REPO}/actions/runs/9/artifacts?per_page=100"], NOT_FOUND))
                     ).failing_ids(REPO, "9") is None
    broken = Gh(([ARTIFACTS], {"artifacts": [{"name": artifact(LINUX_CHECK, 1)}]}), downloads({artifact(LINUX_CHECK, 1): "<testsuite"}))
    assert GhPullHub(broken).failing_ids(REPO, "9") is None


def test_an_artifact_with_no_report_file_is_unreadable_and_one_good_report_is_enough():
    def empty(args):
        return ""

    gh = Gh(([ARTIFACTS], {"artifacts": [{"name": artifact(LINUX_CHECK, 1)}]}), (["run", "download"], empty))
    assert GhPullHub(gh).failing_ids(REPO, "9") is None
    gh = Gh(([ARTIFACTS], {"artifacts": [{"name": artifact(LINUX_CHECK, 1)}, {"name": artifact(WINDOWS_CHECK, 1)}]}),
            downloads({artifact(LINUX_CHECK, 1): "<broken", artifact(WINDOWS_CHECK, 1): JUNIT}))
    assert GhPullHub(gh).failing_ids(REPO, "9") == ["tests.t_a::test_red", "tests.t_b::test_boom"]


def test_a_failed_download_and_a_bad_artifact_list_raise():
    gh = Gh(([ARTIFACTS], {"artifacts": [{"name": artifact(LINUX_CHECK, 1)}]}),
            (["run", "download"], GithubError("gh_failed", "run download: no artifact")))
    with pytest.raises(GithubError):
        GhPullHub(gh).failing_ids(REPO, "9")
    with pytest.raises(GithubError) as error:
        GhPullHub(Gh(([ARTIFACTS], "nope"))).failing_ids(REPO, "9")
    assert error.value.code == "gh_bad_output"


WORKFLOW_RUNS = f"repos/{REPO}/actions/workflows/tests.yml/runs?branch=main&per_page=30"


def workflow_run(id, event="push", status="completed", created="2026-11-02T10:00:00Z", attempt=1):
    return {"id": id, "head_sha": f"sha{id}", "event": event, "status": status, "run_attempt": attempt,
            "created_at": created}


def job_of(id, name, status="completed", conclusion="success"):
    return {"id": id, "name": name, "status": status, "conclusion": conclusion}


def test_the_runs_of_the_tests_workflow_on_a_branch_come_newest_first_with_their_required_jobs():
    gh = Gh(([WORKFLOW_RUNS], {"workflow_runs": [
        workflow_run(1, "push", created="2026-11-02T10:00:00Z", attempt=2),
        workflow_run(2, "pull_request", created="2026-11-02T11:00:00Z"),
        workflow_run(3, "schedule", status="in_progress", created="2026-11-03T01:00:00Z"),
        workflow_run(4, "workflow_dispatch", created="2026-11-02T12:00:00Z")]}),
        ([f"repos/{REPO}/actions/runs/1/jobs?per_page=100"], {"jobs": [
            job_of(11, LINUX_CHECK, conclusion="failure"), job_of(12, WINDOWS_CHECK), job_of(13, "lint")]}),
        ([f"repos/{REPO}/actions/runs/3/jobs?per_page=100"], {"jobs": [
            job_of(31, LINUX_CHECK, "in_progress", None)]}),
        ([f"repos/{REPO}/actions/runs/4/jobs?per_page=100"], {"jobs": [
            job_of(41, LINUX_CHECK, conclusion="cancelled")]}))
    found = GhPullHub(gh).branch_runs(REPO, "main")
    assert [(r.run_id, r.sha, r.event, r.attempt, r.jobs) for r in found] == [
        ("3", "sha3", "schedule", 1, {LINUX_CHECK: PENDING, WINDOWS_CHECK: PENDING}),
        ("4", "sha4", "workflow_dispatch", 1, {LINUX_CHECK: NO_RESULT, WINDOWS_CHECK: NO_RESULT}),
        ("1", "sha1", "push", 2, {LINUX_CHECK: FAILED, WINDOWS_CHECK: PASSED})]
    assert found[0].created_at == datetime(2026, 11, 3, 1, tzinfo=UTC)
    assert not gh.called(f"repos/{REPO}/actions/runs/2/jobs?per_page=100")


def test_the_required_jobs_are_the_setting_and_the_latest_job_of_a_name_counts(tmp_path):
    (tmp_path / "settings.json").write_text('{"required_checks": ["unit", "lint"]}', "utf-8")
    gh = Gh(([WORKFLOW_RUNS], {"workflow_runs": [workflow_run(1)]}),
            ([f"repos/{REPO}/actions/runs/1/jobs?per_page=100", "--paginate"],
             json.dumps({"jobs": [job_of(20, "unit", conclusion="failure"), job_of(10, "lint")]})
             + json.dumps({"jobs": [job_of(30, "unit"), job_of(5, LINUX_CHECK)]})))
    [run] = GhPullHub(gh).branch_runs(REPO, "main")
    assert run.jobs == {"unit": PASSED, "lint": PASSED}


def test_branch_runs_with_a_bad_answer_raise_and_a_bad_setting_raises_before_any_call(tmp_path):
    gh = Gh(([WORKFLOW_RUNS], {"workflow_runs": [{"id": 1, "event": "push"}]}))
    with pytest.raises(GithubError) as error:
        GhPullHub(gh).branch_runs(REPO, "main")
    assert error.value.code == "gh_bad_output"
    with pytest.raises(GithubError):
        GhPullHub(Gh(([WORKFLOW_RUNS], "nope"))).branch_runs(REPO, "main")
    (tmp_path / "settings.json").write_text('{"required_checks": []}', "utf-8")
    gh = Gh()
    with pytest.raises(settings.SettingsError):
        GhPullHub(gh).branch_runs(REPO, "main")
    assert gh.calls == []


# --- conflicts --------------------------------------------------------------------------------


class Git:
    """Answers git calls in order; records them and whether the cache existed at each call."""

    def __init__(self, *done):
        self.done = list(done)
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(args)
        return self.done.pop(0)


def cache_of(tmp_path):
    return tmp_path / "data" / "merge-cache" / "owner-x__repo-y.git"


def test_the_conflicting_files_are_read_from_merge_tree_after_a_fetch_into_a_cache_repo(tmp_path):
    cache = cache_of(tmp_path)
    git = Git(GitDone(0, "", ""), GitDone(0, "", ""),
              GitDone(1, "tree-id\0b/two.py\0a.py\0b/two.py\0\0", ""))
    found = GhPullHub(Gh(), git).conflict_files("Owner-X/Repo-Y", "main", 7)
    assert found == ["a.py", "b/two.py"]
    assert git.calls[0] == ["init", "--bare", "--quiet", str(cache)]
    assert git.calls[1] == [
        "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", "-C", str(cache),
        "fetch", "--quiet", "--no-tags", "https://github.com/Owner-X/Repo-Y.git",
        "+refs/heads/main:refs/orch/base", "+refs/pull/7/head:refs/orch/pr"]
    assert git.calls[2] == ["-C", str(cache), "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z",
                            "refs/orch/base", "refs/orch/pr"]


def test_a_clean_merge_has_no_files_and_an_existing_cache_is_not_made_again(tmp_path):
    cache_of(tmp_path).mkdir(parents=True)
    git = Git(GitDone(0, "", ""), GitDone(0, "tree-id\n", ""))
    assert GhPullHub(Gh(), git).conflict_files(REPO, "main", 7) == []
    assert [c[c.index("-C") + 2] for c in git.calls] == ["fetch", "merge-tree"]


def test_a_git_that_fails_is_a_gh_failed_error(tmp_path):
    for done in ([GitDone(128, "", "no such repo")], [GitDone(0, "", ""), GitDone(128, "", "fatal: no remote ref")],
                 [GitDone(0, "", ""), GitDone(0, "", ""), GitDone(2, "", "usage")]):
        git = Git(*done)
        with pytest.raises(GithubError) as error:
            GhPullHub(Gh(), git).conflict_files(REPO, "main", 7)
        assert error.value.code == "gh_failed"
        shutil.rmtree(tmp_path / "data", ignore_errors=True)


def test_a_repo_not_written_owner_slash_name_is_refused_before_any_git(tmp_path):
    git = Git()
    with pytest.raises(GithubError) as error:
        GhPullHub(Gh(), git).conflict_files("../escape", "main", 7)
    assert error.value.code == "bad_repo"
    assert git.calls == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_real_git_names_the_files_two_local_commits_conflict_in(tmp_path, monkeypatch):
    def git(folder, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=folder, check=True,
                       capture_output=True)

    work, source = tmp_path / "work", tmp_path / "source.git"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "a.txt").write_text("1\n2\n3\n")
    (work / "b.txt").write_text("1\n2\n3\n")
    (work / "keep.txt").write_text("k\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "base")
    git(work, "checkout", "-q", "-b", "pr")
    (work / "a.txt").write_text("1\nPR\n3\n")
    (work / "b.txt").write_text("1\nPR\n3\n")
    (work / "new.txt").write_text("n\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "pr")
    git(work, "checkout", "-q", "-b", "clean", "main")
    (work / "other.txt").write_text("o\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "clean")
    git(work, "checkout", "-q", "main")
    (work / "a.txt").write_text("1\nMAIN\n3\n")
    (work / "b.txt").write_text("1\nMAIN\n3\n")
    git(work, "commit", "-q", "-a", "-m", "main")
    subprocess.run(["git", "init", "-q", "--bare", str(source)], check=True, capture_output=True)
    git(work, "push", "-q", str(source), "main:refs/heads/main", "pr:refs/pull/7/head", "clean:refs/pull/8/head")

    hub = GhPullHub(Gh())
    monkeypatch.setattr(hub, "remote", lambda repo: str(source))
    assert hub.conflict_files(REPO, "main", 7) == ["a.txt", "b.txt"]
    assert hub.conflict_files(REPO, "main", 8) == []  # the cache made by the first call is used again
    assert cache_of(tmp_path).is_dir()


# --- issues -----------------------------------------------------------------------------------


def test_the_merged_pull_requests_of_a_commit_are_their_numbers_sorted():
    gh = Gh(([f"repos/{REPO}/commits/abc/pulls?per_page=100", "--paginate"],
             json.dumps([{"number": 9, "merged_at": "2026-11-02T10:00:00Z"}, {"number": 4, "merged_at": None}])
             + json.dumps([{"number": 3, "merged_at": "2026-11-01T10:00:00Z"}])),
            ([f"repos/{REPO}/commits/none/pulls?per_page=100"], []),
            ([f"repos/{REPO}/commits/gone/pulls?per_page=100"], NOT_FOUND),
            ([f"repos/{REPO}/commits/bad/pulls?per_page=100"], "nope"))
    hub = GhPullHub(gh)
    assert hub.pulls_of_commit(REPO, "abc") == [3, 9]
    assert hub.pulls_of_commit(REPO, "none") == []
    assert hub.pulls_of_commit(REPO, "gone") == []
    with pytest.raises(GithubError) as error:
        hub.pulls_of_commit(REPO, "bad")
    assert error.value.code == "gh_bad_output"


def test_the_open_issues_of_a_label_are_listed_from_every_page_without_pull_requests_and_the_label_is_quoted():
    pages = json.dumps([{"number": 8}, {"number": 3, "pull_request": {}}]) + json.dumps([{"number": 2}])
    gh = Gh(([f"repos/{REPO}/issues?labels=red%20main%2Fci&state=open&per_page=100", "--paginate"], pages))
    assert GhPullHub(gh).issues_with_label(REPO, "red main/ci") == [2, 8]
    with pytest.raises(GithubError) as error:
        GhPullHub(Gh(([f"repos/{REPO}/issues?labels=x&state=open&per_page=100"], '{"message": "x"}'))
                  ).issues_with_label(REPO, "x")
    assert error.value.code == "gh_bad_output"


def test_an_issue_is_created_with_its_labels_and_its_number_returned_and_closed_with_a_reason():
    gh = Gh((["POST", f"repos/{REPO}/issues"], {"number": 77, "html_url": "u"}),
            (["PATCH", f"repos/{REPO}/issues/77"], {"number": 77}))
    hub = GhPullHub(gh)
    assert hub.create_issue(REPO, "Main is red", "details " * 3000, ["main-red", "bug"]) == 77
    hub.close_issue(REPO, 77, "not_planned")
    assert gh.inputs == [{"title": "Main is red", "body": "details " * 3000, "labels": ["main-red", "bug"]},
                         {"state": "closed", "state_reason": "not_planned"}]


@pytest.mark.parametrize("answer", ["", "nope", "{}", '{"number": "7"}'])
def test_an_issue_answer_without_a_number_is_bad_output(answer):
    with pytest.raises(GithubError) as error:
        GhPullHub(Gh((["POST"], answer))).create_issue(REPO, "t", "b", [])
    assert error.value.code == "gh_bad_output"


def test_a_failed_issue_write_raises():
    with pytest.raises(GithubError):
        GhPullHub(Gh((["POST"], failed(403)))).create_issue(REPO, "t", "b", [])
    with pytest.raises(GithubError):
        GhPullHub(Gh((["PATCH"], failed(410)))).close_issue(REPO, 1, "completed")


def test_actions_enabled_reads_the_permission_and_a_repo_without_it_is_off():
    permissions = f"repos/{REPO}/actions/permissions"
    assert GhPullHub(Gh(([permissions], {"enabled": True, "allowed_actions": "all"}))).actions_enabled(REPO) is True
    assert GhPullHub(Gh(([permissions], {"enabled": False}))).actions_enabled(REPO) is False
    assert GhPullHub(Gh(([permissions], NOT_FOUND))).actions_enabled(REPO) is False
    # the workflow's own token has no administration scope: GitHub answers 403, and a sweep that runs there is
    # running on Actions, so a refusal reads as "on"; the login of a person is unchanged (answers above)
    refused = failed(403, "Resource not accessible by integration")
    assert GhPullHub(Gh(([permissions], refused))).actions_enabled(REPO) is True
    # any other 403 is not the workflow token's refusal and still raises
    for text in ("API rate limit exceeded for user ID 1", "Must have admin rights to Repository"):
        with pytest.raises(GithubError):
            GhPullHub(Gh(([permissions], failed(403, text)))).actions_enabled(REPO)
    for answer in ("nope", "{}", '{"enabled": "yes"}'):
        with pytest.raises(GithubError) as error:
            GhPullHub(Gh(([permissions], answer))).actions_enabled(REPO)
        assert error.value.code == "gh_bad_output"
    with pytest.raises(GithubError):
        GhPullHub(Gh(([permissions], failed(500)))).actions_enabled(REPO)
