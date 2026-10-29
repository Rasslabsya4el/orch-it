"""`guards install` (#395 decision 6, row 9): it sets git's `core.hooksPath` to the repo's tracked
hooks folder, never overwrites another value (S09-041), is idempotent, reports whether the Claude
Code PreToolUse entry is in `.claude/settings.json`, and writes one `check` event `guard_install`
with the facts."""

import json
import os
import subprocess

import pytest

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.guards import install
from repo_templates import clone_of
from scan_repos import NO_WINDOW

CLAUDE_SETTINGS = {"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [
    {"type": "command", "command": "python -m orch_it hook pre-tool-use"}]}]}}


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text('{"repo": "acme-x/widgets-y"}', "utf-8")  # the repo the events are written under
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")


def config(work_tree, *args, check=True):
    done = subprocess.run(["git", "-C", str(work_tree), "config", *args], capture_output=True, text=True,
                          creationflags=NO_WINDOW)
    assert not check or done.returncode == 0
    return done.stdout.strip()


@pytest.fixture
def clone(tmp_path, templates):
    return clone_of(tmp_path, {"hooks/pre-push": "#!/bin/sh\nexit 0\n",
                               ".claude/settings.json": json.dumps(CLAUDE_SETTINGS)}, templates)


def check_rows(store):
    return store.query("SELECT kind, step, run, \"check\", target, outcome, findings, facts FROM events"
                       " WHERE step = 'check' ORDER BY at").rows


def test_install_in_a_fresh_clone_sets_core_hooks_path_to_the_tracked_hooks_folder(clone, store):
    assert config(clone, "--get", "core.hooksPath", check=False) == ""
    done = install.install(clone, store, repo="o/n")
    assert config(clone, "--get", "core.hooksPath") == "hooks"
    assert (done.hooks_path, done.changed, done.claude_hook) == ("hooks", True, True)


def test_install_writes_one_guard_install_check_with_the_facts(clone, store):
    install.install(clone, store, repo="o/n")
    started, ended = check_rows(store)
    assert started[:2] == ("step.started", "check") and ended[:2] == ("step.ended", "check")
    assert started[2] == ended[2] and started[2]
    assert started[3:5] == ("guard_install", "o/n")
    assert ended[5] == "passed"
    assert json.loads(ended[6]) == []
    assert json.loads(ended[7]) == {"hooks_path": "hooks", "changed": True, "pre_push_hook": True,
                                    "claude_hook": True}


def test_install_twice_changes_nothing_and_says_it_was_already_installed(clone, store):
    install.install(clone, store, repo="o/n")
    again = install.install(clone, store, repo="o/n")
    assert (again.hooks_path, again.changed) == ("hooks", False)
    assert config(clone, "--get-all", "core.hooksPath") == "hooks"
    last = check_rows(store)[-1]
    assert json.loads(last[7])["changed"] is False


@pytest.mark.parametrize("spelling", ["./hooks", "hooks/"])
def test_our_folder_in_another_spelling_is_ours(clone, store, spelling):
    config(clone, "core.hooksPath", spelling)
    assert install.install(clone, store, repo="o/n").changed is False
    assert config(clone, "--get", "core.hooksPath") == spelling


def test_the_absolute_path_of_our_folder_is_ours(clone, store):
    config(clone, "core.hooksPath", str(clone / "hooks"))
    assert install.install(clone, store, repo="o/n").changed is False


def test_another_core_hooks_path_is_refused_by_name_and_left_as_it_was(clone, store):
    taken = "/elsewhere/other-" + "hooks"
    config(clone, "core.hooksPath", taken)
    with pytest.raises(install.InstallError) as refused:
        install.install(clone, store, repo="o/n")
    assert refused.value.code == "hooks_path_taken"
    assert taken in str(refused.value)
    assert config(clone, "--get", "core.hooksPath") == taken
    ended = check_rows(store)[-1]
    assert ended[5] == "refused"
    assert json.loads(ended[6]) == ["hooks_path_taken"]
    assert json.loads(ended[7])["hooks_path"] == taken


def test_a_core_hooks_path_of_the_user_config_is_another_value_too(clone, store, tmp_path, monkeypatch):
    user_config = tmp_path / "user-gitconfig"
    user_config.write_text("[core]\n\thooksPath = /user/hooks\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(user_config))
    with pytest.raises(install.InstallError) as refused:
        install.install(clone, store, repo="o/n")
    assert refused.value.code == "hooks_path_taken"
    assert config(clone, "--local", "--get", "core.hooksPath", check=False) == ""


def test_the_claude_hook_is_reported_missing_when_settings_hold_no_pre_tool_use_entry(tmp_path, store, templates):
    clone = clone_of(tmp_path, {"hooks/pre-push": "#!/bin/sh\n",
                                ".claude/settings.json": json.dumps({"hooks": {"SessionStart": []}})}, templates)
    assert install.install(clone, store, repo="o/n").claude_hook is False
    ended = check_rows(store)[-1]
    assert json.loads(ended[7])["claude_hook"] is False
    assert (ended[5], json.loads(ended[6])) == ("failed", ["claude_hook_missing"])


@pytest.mark.parametrize("settings_text", [None, "not json", "[]", '{"hooks": []}',
                                           '{"hooks": {"PreToolUse": "x"}}',
                                           '{"hooks": {"PreToolUse": [{"hooks": [{"command": "other"}]}]}}'])
def test_a_settings_file_that_holds_no_entry_is_no_hook(tmp_path, store, settings_text, templates):
    files = {"hooks/pre-push": "#!/bin/sh\n"}
    if settings_text is not None:
        files[".claude/settings.json"] = settings_text
    clone = clone_of(tmp_path, files, templates)
    assert install.install(clone, store, repo="o/n").claude_hook is False


def test_a_repo_without_the_tracked_hook_file_cannot_be_installed(tmp_path, store, templates):
    clone = clone_of(tmp_path, {"README.md": "x\n"}, templates)
    with pytest.raises(install.InstallError) as refused:
        install.install(clone, store, repo="o/n")
    assert refused.value.code == "hook_file_missing"
    assert config(clone, "--get", "core.hooksPath", check=False) == ""


def test_a_folder_that_is_no_repo_cannot_be_installed(tmp_path, store):
    (tmp_path / "plain").mkdir()
    with pytest.raises(install.InstallError) as refused:
        install.install(tmp_path / "plain", store, repo="o/n")
    assert refused.value.code == "not_a_repo"


def test_install_works_from_a_sub_folder_of_the_work_tree(clone, store):
    (clone / "src").mkdir()
    install.install(clone / "src", store, repo="o/n")
    assert config(clone, "--get", "core.hooksPath") == "hooks"


def test_the_command_installs_prints_a_short_summary_and_exits_0(clone, tmp_path, capsys):
    code = main(["--store", str(tmp_path / "store"), "guards", "install", "--work-tree", str(clone)])
    out = capsys.readouterr().out
    assert code == 0
    assert "pre-push hook: installed" in out and "core.hooksPath=hooks" in out
    assert "claude code hook: present" in out
    assert config(clone, "--get", "core.hooksPath") == "hooks"
    assert main(["--store", str(tmp_path / "store"), "guards", "install", "--work-tree", str(clone)]) == 0
    assert "already installed" in capsys.readouterr().out


def test_the_command_refuses_another_core_hooks_path_with_exit_2_and_names_it(clone, tmp_path, capsys):
    taken = "/elsewhere/other-" + "hooks"
    config(clone, "core.hooksPath", taken)
    code = main(["--store", str(tmp_path / "store"), "guards", "install", "--work-tree", str(clone)])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "error: hooks_path_taken" in captured.err and taken in captured.err
    assert config(clone, "--get", "core.hooksPath") == taken
