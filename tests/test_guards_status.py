"""`guards status` (#395 decision 5, row 9): `main`'s branch protection is read, never changed, and the
six facts of the decision are reported as a table and as three `check` events (`branch_protection`,
`hooks_path`, `guard_install`): the required checks are exactly `linux` and `windows`, the branch
is strict, admins are enforced, force push and deletion are off, the pre-push hook is installed and
the Claude Code hook is installed. A protection that cannot be read is reported, never a crash."""

import json
import os
import subprocess

import pytest

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.guards import install, status
from orch_it.testruns import github
from repo_templates import clone_of
from scan_repos import NO_WINDOW, Repo
from required_names import LINUX_CHECK, WINDOWS_CHECK

CLAUDE_SETTINGS = {"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [
    {"type": "command", "command": "python -m orch_it hook pre-tool-use"}]}]}}
FACTS = ("required_checks", "strict", "enforce_admins", "force_push_and_deletion_off", "pre_push_hook",
         "claude_hook")


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def protection(**changes):
    """What `gh api repos/<repo>/branches/main/protection` answers for orch-it's own protection."""

    answer = {
        "required_status_checks": {"strict": True, "contexts": [LINUX_CHECK, WINDOWS_CHECK],
                                   "checks": [{"context": LINUX_CHECK, "app_id": 15368},
                                              {"context": WINDOWS_CHECK, "app_id": 15368}]},
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }
    return answer | changes


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")


@pytest.fixture
def installed(tmp_path, store, templates):
    """A clone with both hooks wired."""

    target = clone_of(tmp_path, {"hooks/pre-push": "#!/bin/sh\n", ".claude/settings.json": json.dumps(CLAUDE_SETTINGS)},
                      templates)
    install.install(target, store, repo="o/n")
    return target


@pytest.fixture
def bare(tmp_path, templates):
    """A clone that wires nothing."""

    return clone_of(tmp_path, {"README.md": "x\n"}, templates, name="plain")


def reading(answer):
    seen = []

    def reader(repo):
        seen.append(repo)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return reader, seen


def run(store, work_tree, answer, repo="o/n"):
    reader, seen = reading(answer)
    return status.status(store, repo, work_tree, protection=reader), seen


def checks(store):
    """The last `check` step of each code: check code -> (outcome, findings, facts, target)."""

    rows = store.query(
        "SELECT s.\"check\", e.outcome, e.findings, e.facts, s.target FROM events s"
        " JOIN events e ON e.run = s.run AND e.kind = 'step.ended' WHERE s.kind = 'step.started'"
        " AND s.step = 'check' ORDER BY s.at").rows
    return {code: (outcome, json.loads(findings), json.loads(facts), target)
            for code, outcome, findings, facts, target in rows}


def test_a_protected_repo_with_both_hooks_reports_all_six_facts_ok(installed, store):
    (report, seen) = run(store, installed, protection())
    assert seen == ["o/n"]
    assert [(f.name, f.ok) for f in report.facts] == [(name, True) for name in FACTS]
    assert report.ok is True


def test_the_protected_repo_writes_three_passed_checks_with_their_facts(installed, store):
    run(store, installed, protection())
    written = {code: row for code, row in checks(store).items()}
    assert set(written) == {"branch_protection", "hooks_path", "guard_install"}
    for code, (outcome, findings, facts, target) in written.items():
        assert (outcome, findings, target) == ("passed", [], "o/n"), code
    assert written["branch_protection"][2] == {
        "required_checks": [LINUX_CHECK, WINDOWS_CHECK], "strict": True, "enforce_admins": True,
        "allow_force_pushes": False, "allow_deletions": False}
    assert written["hooks_path"][2] == {"hooks_path": "hooks", "ours": True, "pre_push_file": True}
    assert written["guard_install"][2] == {"pre_push_hook": True, "claude_hook": True}


def test_a_repo_with_no_protection_and_no_hooks_reports_every_fact_failed(bare, store):
    unprotected = github.GithubError("gh_failed", "api repos/o/n/branches/main/protection: Branch not protected (HTTP 404)")
    report, _ = run(store, bare, unprotected)
    assert [(f.name, f.ok) for f in report.facts] == [(name, False) for name in FACTS]
    assert report.ok is False
    written = checks(store)
    assert written["branch_protection"][:2] == ("failed", ["branch_unprotected"])
    assert written["hooks_path"][:2] == ("failed", ["hooks_path_unset", "pre_push_file_missing"])
    assert written["guard_install"][:2] == ("failed", ["pre_push_hook_missing", "claude_hook_missing"])


def test_a_protection_that_cannot_be_read_is_reported_not_raised(installed, store):
    for answer in (github.GithubError("gh_timeout", "api"), github.GithubError("gh_failed", "no auth"),
                   RuntimeError("boom"), ["not", "a", "mapping"], None):
        report, _ = run(store, installed, answer)
        protected = [f for f in report.facts if f.name in FACTS[:4]]
        assert [f.ok for f in protected] == [False] * 4, answer
        assert [f.ok for f in report.facts[4:]] == [True, True]
    assert checks(store)["branch_protection"][1] == ["protection_unreadable"]


@pytest.mark.parametrize("changes, failed, finding", [
    ({"required_status_checks": {"strict": True, "contexts": [LINUX_CHECK]}}, "required_checks", "required_checks_wrong"),
    ({"required_status_checks": {"strict": True, "contexts": [LINUX_CHECK, WINDOWS_CHECK, "lint"]}},
     "required_checks", "required_checks_wrong"),
    ({"required_status_checks": {"strict": True, "contexts": [], "checks": [{"context": LINUX_CHECK}]}},
     "required_checks", "required_checks_wrong"),
    ({"required_status_checks": {"strict": False, "contexts": [LINUX_CHECK, WINDOWS_CHECK]}}, "strict", "not_strict"),
    ({"enforce_admins": {"enabled": False}}, "enforce_admins", "admins_not_enforced"),
    ({"allow_force_pushes": {"enabled": True}}, "force_push_and_deletion_off", "force_push_allowed"),
    ({"allow_deletions": {"enabled": True}}, "force_push_and_deletion_off", "deletion_allowed"),
    ({"allow_force_pushes": {}}, "force_push_and_deletion_off", "force_push_allowed"),
])
def test_each_weakened_protection_fails_its_own_fact_and_only_that_one(installed, store, changes, failed, finding):
    report, _ = run(store, installed, protection(**changes))
    assert [f.name for f in report.facts if not f.ok] == [failed]
    assert finding in checks(store)["branch_protection"][1]


def test_the_required_checks_may_be_named_only_by_the_newer_checks_list(installed, store):
    answer = protection(required_status_checks={"strict": True, "contexts": [],
                                                "checks": [{"context": WINDOWS_CHECK}, {"context": LINUX_CHECK}]})
    report, _ = run(store, installed, answer)
    assert report.ok is True


def test_a_hooks_path_of_another_value_is_not_our_pre_push_hook(installed, store):
    subprocess.run(["git", "-C", str(installed), "config", "core.hooksPath", "/elsewhere/h"], check=True,
                   creationflags=NO_WINDOW)
    report, _ = run(store, installed, protection())
    assert [f.name for f in report.facts if not f.ok] == ["pre_push_hook"]
    outcome, findings, facts, _ = checks(store)["hooks_path"]
    assert (outcome, findings) == ("failed", ["hooks_path_other"])
    assert facts["hooks_path"] == "/elsewhere/h" and facts["ours"] is False


def test_a_folder_that_is_no_repo_fails_the_hook_facts_and_still_reports(tmp_path, store):
    (tmp_path / "nowhere").mkdir()
    report, _ = run(store, tmp_path / "nowhere", protection())
    assert [(f.name, f.ok) for f in report.facts][:4] == [(n, True) for n in FACTS[:4]]
    assert [f.ok for f in report.facts[4:]] == [False, False]
    assert "not_a_repo" in checks(store)["hooks_path"][1]


def test_status_changes_nothing_in_the_repo_or_on_github(installed, store, monkeypatch):
    calls = []
    monkeypatch.setattr(github, "run_gh", lambda args: calls.append(args) or json.dumps(protection()))
    before = subprocess.run(["git", "-C", str(installed), "config", "--local", "--list"], capture_output=True,
                            text=True, creationflags=NO_WINDOW).stdout
    status.status(store, "o/n", installed)
    assert calls == [["api", "repos/o/n/branches/main/protection"]]
    after = subprocess.run(["git", "-C", str(installed), "config", "--local", "--list"], capture_output=True,
                           text=True, creationflags=NO_WINDOW).stdout
    assert before == after


def test_the_default_reader_reports_a_gh_answer_that_is_no_json_as_unreadable(installed, store, monkeypatch):
    monkeypatch.setattr(github, "run_gh", lambda args: "<html>")
    report, _ = run_default(store, installed)
    assert report.ok is False
    assert checks(store)["branch_protection"][1] == ["protection_unreadable"]


def run_default(store, work_tree):
    return status.status(store, "o/n", work_tree), None


def test_the_command_prints_the_six_facts_as_a_table_and_exits_0_when_all_are_ok(installed, tmp_path,
                                                                                 monkeypatch, capsys):
    monkeypatch.setattr(github, "run_gh", lambda args: json.dumps(protection()))
    code = main(["--store", str(tmp_path / "store"), "guards", "status", "--repo", "o/n",
                 "--work-tree", str(installed)])
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert out[0].split() == ["fact", "ok", "detail"]
    assert [line.split()[0] for line in out[1:7]] == list(FACTS)  # the six rows; names only GitHub runs follow them
    assert all(line.split()[1] == "yes" for line in out[1:7])


def test_the_command_exits_1_and_still_prints_the_table_when_a_fact_fails(bare, tmp_path, monkeypatch, capsys):
    def refuse(args):
        raise github.GithubError("gh_failed", "api: Branch not protected (HTTP 404)")

    monkeypatch.setattr(github, "run_gh", refuse)
    code = main(["--store", str(tmp_path / "store"), "guards", "status", "--repo", "o/n",
                 "--work-tree", str(bare)])
    out = capsys.readouterr().out.splitlines()
    assert code == 1
    assert [line.split()[1] for line in out[1:7]] == ["no"] * 6
    assert "unprotected" in out[1] or "not protected" in out[1]


def test_the_repo_defaults_to_the_origin_of_the_work_tree(tmp_path, monkeypatch, capsys):
    repo = Repo(tmp_path / "gh")
    repo.commit({"a.txt": "x\n"})
    repo.git("remote", "add", "origin", "https://github.com/Own-er/na.me.git")
    seen = []
    monkeypatch.setattr(github, "run_gh", lambda args: seen.append(args) or json.dumps(protection()))
    main(["--store", str(tmp_path / "store"), "guards", "status", "--work-tree", str(repo.path)])
    assert seen == [["api", "repos/Own-er/na.me/branches/main/protection"]]


def command_output(tmp_path, monkeypatch, capsys, work_tree, answer):
    """The text `guards status` prints when GitHub answers `answer` (a protection, or the error it gives)."""

    def read(args):
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer)

    monkeypatch.setattr(github, "run_gh", read)
    main(["--store", str(tmp_path / "store"), "guards", "status", "--repo", "o/n", "--work-tree", str(work_tree)])
    return capsys.readouterr().out


def test_no_line_of_guards_status_says_gate_and_names_the_required_checks_holder_on_a_protected_base(
        installed, tmp_path, monkeypatch, capsys):
    out = command_output(tmp_path, monkeypatch, capsys, installed, protection())
    assert "required checks held by GitHub's branch protection" in out
    assert not any("gate" in line.lower() for line in out.splitlines())


def test_no_line_of_guards_status_says_gate_and_names_the_required_checks_holder_on_an_unprotected_base(
        bare, tmp_path, monkeypatch, capsys):
    out = command_output(tmp_path, monkeypatch, capsys, bare,
                         github.GithubError("gh_failed", "api: Branch not protected (HTTP 404)"))
    assert "required checks held by the merge line, not by GitHub" in out
    assert not any("gate" in line.lower() for line in out.splitlines())
