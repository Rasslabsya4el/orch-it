"""The tickets hook judges a ticket in the language of the repo the `gh` call is for (#464 unit 2, rows 1-5 and 9).

The target of a call: the repo of the `gh` call (`--repo` / `-R`, else `GH_REPO`), else the repo of the `cd` folder or
the payload cwd. The repo of the tree at hand reads that tree's `settings.json`; another repo reads its `settings.json`
on its default branch through the hub (not found is English). A settings file that is no JSON object, or a read that
fails but not-found, is a named bad setting: the hook denies naming the file or the repo, create and edit alike.
Invented values only: repos from `fake_hub`, trees made here, a hub stub that holds the files."""

import json
import shlex
from pathlib import Path

import pytest
from fake_hub import REPO, FakeHub
from language_tree import git_tree, write_language
from ticket_fixtures import fixture

from orch_it import settings
from orch_it.guards.values import Values
from orch_it.testruns.github import GithubError
from orch_it.tickets import hook

TRANSCRIPT = Path(__file__).parent / "fixtures" / "transcripts" / "write_ticket_called.jsonl"
BODY_RU = fixture("good_basic")[1]
TITLE = "Cart shows the shipping cost before payment"
OTHER = "owner-z/repo-b"
WRONG = "human_field_wrong_language"

pytestmark = pytest.mark.os_touching  # git trees, paths and the hook's own files (#414 unit 4)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_STORE", str(tmp_path / "events"))
    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.setattr("orch_it.guards.values.read_values", lambda **kwargs: Values())
    monkeypatch.setattr(hook, "repo_of", lambda folder: REPO)
    monkeypatch.setattr(hook, "_record", lambda *args, **kwargs: None)


@pytest.fixture
def trees(tmp_path):
    ru = git_tree(tmp_path / "ru")
    write_language(ru, "ru")
    return ru, git_tree(tmp_path / "en")


@pytest.fixture
def hub(monkeypatch):
    h = FakeHub()
    h.add(10, BODY_RU, ["ready-for-agent"], title=TITLE, repo=OTHER)
    h.add(10, BODY_RU, ["ready-for-agent"], title=TITLE)
    monkeypatch.setattr(hook, "make_hub", lambda: h)
    return h


def payload(command, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
            "cwd": str(cwd), "transcript_path": str(TRANSCRIPT)}


def body_file(tmp_path, body=BODY_RU):
    path = tmp_path / "body.md"
    path.write_text(body, "utf-8")
    return shlex.quote(str(path))


def create(tmp_path, repo=REPO, flag="--repo"):
    return f"gh issue create {flag} {repo} --title '{TITLE}' --body-file {body_file(tmp_path)}"


def edit(tmp_path, repo=OTHER):
    return f"gh issue edit 10 --repo {repo} --body-file {body_file(tmp_path)}"


def judge(hub, command, cwd):
    # the hook's entry point names the payload's folder once; `judge` is called under that target
    with settings.target(folder=cwd):
        return hook.judge(payload(command, cwd), hub=hub, repo_of=lambda folder: REPO)


def codes(refusal):
    return refusal.codes if refusal else []


def denied(capsys):
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else None


def test_row_1_the_process_in_a_ru_tree_and_the_payload_cwd_en_judges_en(trees, tmp_path, monkeypatch, hub):
    ru, en = trees
    monkeypatch.chdir(ru)
    assert WRONG in codes(judge(hub, create(tmp_path), en))


def test_row_2_the_process_in_an_en_tree_and_the_payload_cwd_ru_judges_ru(trees, tmp_path, monkeypatch, hub):
    ru, en = trees
    monkeypatch.chdir(en)
    assert WRONG not in codes(judge(hub, create(tmp_path), ru))


def test_row_3_a_cd_into_a_ru_tree_judges_ru(trees, tmp_path, monkeypatch, hub):
    ru, en = trees
    monkeypatch.chdir(en)
    assert WRONG not in codes(judge(hub, f"cd {shlex.quote(str(ru))} && {create(tmp_path)}", en))


def test_row_4_another_repo_is_judged_in_its_default_branch_language(trees, tmp_path, hub):
    _, en = trees
    hub.files[(OTHER, "settings.json")] = json.dumps({"human_language": "ru"})
    assert WRONG not in codes(judge(hub, create(tmp_path, OTHER), en))
    assert WRONG not in codes(judge(hub, create(tmp_path, OTHER, flag="-R"), en))


def test_row_4_another_repo_with_no_settings_file_is_english_whatever_the_tree_says(trees, tmp_path, hub):
    ru, _ = trees
    assert WRONG in codes(judge(hub, create(tmp_path, OTHER), ru))  # not found: en; the ru tree at hand is not B


def test_row_4_gh_repo_names_the_target_as_gh_picks_it(trees, tmp_path, monkeypatch, hub):
    _, en = trees
    hub.files[(OTHER, "settings.json")] = json.dumps({"human_language": "ru"})
    monkeypatch.setenv("GH_REPO", OTHER)
    command = f"gh issue create --title '{TITLE}' --body-file {body_file(tmp_path)}"
    assert WRONG not in codes(judge(hub, command, en))


def test_the_flag_repo_that_is_the_repo_of_the_tree_reads_the_tree_not_the_hub(trees, tmp_path, hub):
    ru, _ = trees
    hub.files[(REPO, "settings.json")] = json.dumps({"human_language": "en"})  # the hub would say en; the tree ru
    assert WRONG not in codes(judge(hub, create(tmp_path, REPO), ru))


@pytest.mark.parametrize("text", ["{", "[]"])
def test_row_5_an_unreadable_tree_file_is_denied_naming_the_file_for_create_and_edit(
        trees, tmp_path, capsys, hub, text):
    _, en = trees
    (en / "settings.json").write_text(text, "utf-8")
    for command in (create(tmp_path, REPO), edit(tmp_path, REPO)):
        assert hook.main(json.dumps(payload(command, en))) == 0
        reason = denied(capsys)
        assert reason is not None, command
        assert "settings_unreadable" in reason
        assert str(en / "settings.json") in reason


@pytest.mark.parametrize("make_command", [create, edit])
def test_row_5_a_failed_read_of_the_other_repo_is_denied_naming_the_repo(
        trees, tmp_path, capsys, hub, make_command):
    _, en = trees
    hub.files[(OTHER, "settings.json")] = GithubError("gh_failed", "HTTP 500")
    assert hook.main(json.dumps(payload(make_command(tmp_path, OTHER), en))) == 0
    reason = denied(capsys)
    assert reason is not None
    assert "settings_unreadable" in reason
    assert f"{OTHER}:settings.json" in reason


def test_row_5_a_file_of_the_other_repo_that_is_no_object_is_denied_naming_the_repo(trees, tmp_path, capsys, hub):
    _, en = trees
    hub.files[(OTHER, "settings.json")] = "["
    assert hook.main(json.dumps(payload(create(tmp_path, OTHER), en))) == 0
    assert f"{OTHER}:settings.json" in denied(capsys)


def test_a_bad_setting_keeps_the_codes_of_the_calls_before_it(trees, tmp_path, hub):
    _, en = trees
    hub.files[(OTHER, "settings.json")] = GithubError("gh_failed", "HTTP 500")
    too_long = f"gh issue edit 10 --repo {OTHER} --title '{'x' * 300}'"
    refusal = judge(hub, f"{too_long} && {create(tmp_path, OTHER)}", en)
    assert "title_too_long" in refusal.codes
    assert refusal.codes[-1] == "bad_setting"


def test_row_9_an_edit_in_another_repo_is_judged_in_its_language(trees, tmp_path, hub):
    _, en = trees
    assert WRONG in codes(judge(hub, edit(tmp_path), en))  # B has no file: en, the Russian block is refused
    hub.files[(OTHER, "settings.json")] = json.dumps({"human_language": "ru"})
    assert WRONG not in codes(judge(hub, edit(tmp_path), en))


def test_the_other_repo_s_file_is_read_once_for_one_call(trees, tmp_path, hub):
    _, en = trees
    asked = []
    real = hub.file
    hub.file = lambda repo, path: (asked.append((repo, path)), real(repo, path))[1]
    judge(hub, create(tmp_path, OTHER), en)
    assert asked == [(OTHER, "settings.json")]
