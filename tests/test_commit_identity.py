"""The commit identity of orch-it's own commits and clones: new commits made by orch-it carry the
GitHub noreply identity while the repo's setting `commit_identity` is `noreply`, and `guards scan`
refuses a commit whose author or committer e-mail is no noreply address.

Seams under test: `identity` (the identity, the setting, `is_noreply`), `cli.main(["guards", "scan",
...])` on a temp repo, `remote._clone` and `remote._setup` (the far side's clone and reused slot)
and `update._commit_skills` (the commit-tree of `skills update`). Invented values only: login
`example-user`, id `12345`, repo `acme/example`.
"""

import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from orch_it import identity, settings
from orch_it.cli import main
from orch_it.skills import update
from orch_it.testruns import remote

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOGIN = "example-user"
USER_ID = 12345
NOREPLY = f"{USER_ID}+{LOGIN}@users.noreply.github.com"
REPO = "acme/example"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
                          timeout=60, creationflags=NO_WINDOW, env=env).stdout.strip()


def commit(repo: Path, author: str, committer: str, message: str = "subject") -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Author Name", "GIT_AUTHOR_EMAIL": author,
           "GIT_COMMITTER_NAME": "Committer Name", "GIT_COMMITTER_EMAIL": committer}
    git(repo, "commit", "-q", "--allow-empty", "-m", message, env=env)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def no_settings_file(tmp_path, monkeypatch):
    """No settings file unless a test writes one: the default applies."""
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "absent" / "settings.json"))


def write_settings(tmp_path, monkeypatch, data: dict) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), "utf-8")
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))


@dataclass
class Work:
    path: Path
    root: str  # the commit the scanned range starts after


def build_repo(folder: Path) -> str:
    """The template of `repo`: a work tree of the repo, one root commit; returns the root's sha."""

    path = folder / "work"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "remote", "add", "origin", f"https://github.com/{REPO}.git")
    return commit(path, NOREPLY, NOREPLY, "root")


@pytest.fixture
def repo(tmp_path, templates):
    path, root = templates.copy("commit identity: work repo", build_repo, tmp_path / "work", part="work")
    return Work(path, root)


def scan(capsys, work: Work, *extra: str) -> tuple[int, str]:
    code = main(["guards", "scan", "--range", f"{work.root}..HEAD", "--work-tree", str(work.path),
                 "--machine-independent", *extra])
    return code, capsys.readouterr().out


def test_identity_is_built_from_the_gh_user_object():
    assert identity.noreply_identity({"login": LOGIN, "id": USER_ID}) == (LOGIN, NOREPLY)


@pytest.mark.parametrize("address", [NOREPLY, f"{LOGIN}@users.noreply.github.com", "noreply@github.com"])
def test_is_noreply_accepts_the_three_accepted_forms(address):
    assert identity.is_noreply(address)


@pytest.mark.parametrize("address", [
    "someone@example.org",
    f"x+{LOGIN}@users.noreply.github.com",
    f"{LOGIN}@users.noreply.github.com.example.org",
    f"{LOGIN}@github.com",
])
def test_is_noreply_refuses_every_other_address(address):
    assert not identity.is_noreply(address)


def test_absent_settings_file_means_noreply():
    assert identity.mode_of(REPO) == "noreply"


def test_the_setting_any_is_read_per_repo_and_a_wrong_value_is_refused():
    raw = {"repos": {REPO: {"commit_identity": "any"}}}
    assert identity.mode_of(REPO, raw=raw) == "any"
    with pytest.raises(settings.SettingsError) as refused:
        identity.mode_of(REPO, raw={"repos": {REPO: {"commit_identity": "maybe"}}})
    assert refused.value.code == "bad_setting"


def test_a_commit_of_noreply_identities_passes(capsys, repo):
    commit(repo.path, NOREPLY, NOREPLY)
    code, out = scan(capsys, repo)
    assert code == 0
    assert "hits=0" in out


def test_an_ordinary_author_email_fails_and_names_the_author(capsys, repo):
    sha = commit(repo.path, "someone@example.org", NOREPLY)
    code, out = scan(capsys, repo)
    assert code == 1
    assert f"{sha[:12]} author" in out


def test_an_ordinary_committer_email_with_a_noreply_author_fails_and_names_the_committer(capsys, repo):
    sha = commit(repo.path, NOREPLY, "someone@example.org")
    code, out = scan(capsys, repo)
    assert code == 1
    assert f"{sha[:12]} committer" in out
    assert f"{sha[:12]} author" not in out


def test_the_setting_any_passes_an_ordinary_email(capsys, repo, tmp_path, monkeypatch):
    write_settings(tmp_path, monkeypatch, {"repos": {REPO: {"commit_identity": "any"}}})
    commit(repo.path, "someone@example.org", "someone@example.org")
    code, _ = scan(capsys, repo)
    assert code == 0


def test_the_option_no_require_turns_the_check_off(capsys, repo):
    commit(repo.path, "someone@example.org", NOREPLY)
    code, _ = scan(capsys, repo, "--no-require-noreply-email")
    assert code == 0


def test_the_option_require_turns_the_check_on_under_the_setting_any(capsys, repo, tmp_path, monkeypatch):
    write_settings(tmp_path, monkeypatch, {"repos": {REPO: {"commit_identity": "any"}}})
    sha = commit(repo.path, "someone@example.org", NOREPLY)
    code, out = scan(capsys, repo, "--require-noreply-email")
    assert code == 1
    assert f"{sha[:12]} author" in out


def test_the_clone_gets_the_identity_in_its_local_config(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    commit(origin, NOREPLY, NOREPLY, "root")
    slot = tmp_path / "slot"
    remote._clone(str(slot), str(origin), {"name": LOGIN, "email": NOREPLY})
    assert git(slot, "config", "--local", "--get", "user.name") == LOGIN
    assert git(slot, "config", "--local", "--get", "user.email") == NOREPLY


def test_a_reused_slot_gets_the_identity_it_is_given_now(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    sha = commit(origin, NOREPLY, NOREPLY, "root")
    slot = str(tmp_path / "slot")
    request = {"commit": sha, "origin": str(origin), "identity": {"name": LOGIN, "email": NOREPLY}}
    remote._setup(request, slot, threading.Event())  # a fresh clone with the first identity
    other = {"name": "other-user", "email": f"999+other-user@{identity.NOREPLY_DOMAIN}"}
    remote._setup({**request, "identity": other}, slot, threading.Event())  # the reused slot, fetched
    assert git(slot, "config", "--local", "--get", "user.name") == "other-user"
    assert git(slot, "config", "--local", "--get", "user.email") == other["email"]


def test_a_repo_with_no_github_origin_is_checked_as_noreply(tmp_path, monkeypatch):
    write_settings(tmp_path, monkeypatch, {"repos": {REPO: {"commit_identity": "any"}}})
    path = tmp_path / "plain"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    root = commit(path, NOREPLY, NOREPLY, "root")
    commit(path, "someone@example.org", "someone@example.org")
    code = main(["guards", "scan", "--range", f"{root}..HEAD", "--work-tree", str(path),
                 "--machine-independent"])
    assert code == 1  # the default mode applies, not the settings section of a repo the work tree has no origin for


def test_the_all_option_checks_no_commit_email(capsys, repo):
    commit(repo.path, "someone@example.org", "someone@example.org")
    code = main(["guards", "scan", "--all", "--work-tree", str(repo.path), "--machine-independent"])
    out = capsys.readouterr().out
    assert code == 0
    assert "not_noreply" not in out


def test_the_commit_tree_env_carries_the_identity_to_the_commit(repo, tmp_path):
    base = git(repo.path, "rev-parse", "HEAD")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    sha = update._commit_skills(repo.path, base, scratch, "msg", identity=(LOGIN, NOREPLY))
    assert git(repo.path, "log", "-1", "--format=%an|%ae|%cn|%ce", sha) == f"{LOGIN}|{NOREPLY}|{LOGIN}|{NOREPLY}"


def test_a_clone_origin_that_is_github_gets_the_identity_of_the_gh_user(monkeypatch):
    monkeypatch.setattr(identity, "current_user", lambda: {"login": LOGIN, "id": USER_ID})
    assert identity.for_origin(f"https://github.com/{REPO}.git") == {"name": LOGIN, "email": NOREPLY}


def test_a_clone_origin_that_is_no_github_repo_gets_no_identity(tmp_path):
    assert identity.for_origin(str(tmp_path / "origin")) is None
