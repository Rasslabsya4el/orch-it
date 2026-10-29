"""The git pre-push hook's closing check (#422, row 3): a commit a push sends to GitHub whose message
holds a closing keyword before a ticket reference is refused, whatever the remote's visibility; a
`Refs <URL>` or `Ticket: <URL>` line, or a closing word with no reference, passes. The check reads
the commits the remote lacks (the same set the private-data scan reads), a new ref included, and
skips a deleted ref. A git failure of the check lets the push through and says so, and never stops
the private-data scan; a hit always refuses.

The messages are written with the real git of a temporary repository. The visibility is injected:
no test asks GitHub. One test pushes with real git through the tracked `hooks/pre-push` file."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from orch_it.events.store import Store
from orch_it.guards import prepush, scanner
from orch_it.guards.scanner import Patterns
from scan_repos import GITHUB_TOKEN, NO_WINDOW, Repo

ROOT = Path(__file__).resolve().parent.parent
ZEROS = "0" * 40
URL = "https://github.com/owner/name/issues/7"
# the one message the check has to refuse in this repo's history (`git show -s --format=%B 37fa812`)
MESSAGE_37FA812 = (
    "actions_enabled: only the workflow token's 403 reads as on; review fixes\n\n"
    "https://github.com/Rasslabsya4el/orchestration-layer/issues/411 unit 9 review: a rate-limit 403 and a person's\n"
    "login without admin keep raising; two tests for them; docstring says exactly this; the ticket URL and the\n"
    "duplicate test left the permissions test file.")
# what the hook reads off the machine is not under test here: the closing check needs none of it
PATTERNS = Patterns.machine_independent()
REFUSAL_WAY = "write `Refs <URL>` or `Ticket: <URL>`"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")


@pytest.fixture
def repo(tmp_path):
    work = Repo(tmp_path / "work")
    work.base = work.commit({"README.md": "clean\n"}, message="the first work")
    return work


def line(local, remote=ZEROS, ref="refs/heads/main"):
    return f"{ref} {local} {ref} {remote}\n"


def push(repo, store, stdin, *, visibility="public"):
    return prepush.run_pre_push(stdin, store, remote_url="https://example.invalid/o/n.git",
                                remote_name="origin", work_tree=repo.path,
                                visibility=lambda _url: visibility, patterns=PATTERNS)


def events(store):
    return store.query("SELECT kind, guard, verdict, rule_id, segment FROM events ORDER BY at").rows


# --- a keyword before a reference is refused, naming the keyword and the sha -------------------

@pytest.mark.parametrize("visibility", ["public", "private"])
def test_a_keyword_before_a_reference_is_refused_naming_the_keyword_and_the_sha(
        repo, store, capsys, visibility):
    sha = repo.commit({"a.txt": "x\n"}, message=f"work\n\nFixes {URL}")
    assert push(repo, store, line(sha, repo.base), visibility=visibility) == ("", 1)
    err = capsys.readouterr().err
    assert sha in err, err
    assert "Fixes " + URL in err, err
    assert REFUSAL_WAY in err, err


@pytest.mark.parametrize("keyword", ["close", "closes", "closed", "fix", "fixes", "fixed",
                                     "resolve", "resolves", "resolved", "CLOSES", "Fixed"])
def test_each_keyword_form_before_a_reference_is_refused(repo, store, capsys, keyword):
    sha = repo.commit({"a.txt": "x\n"}, message=f"work\n\n{keyword} {URL}")
    assert push(repo, store, line(sha, repo.base)) == ("", 1), keyword
    assert f"{sha}" in capsys.readouterr().err, keyword


def test_a_keyword_on_one_line_and_the_reference_on_the_next_is_refused(repo, store, capsys):
    sha = repo.commit({"a.txt": "x\n"}, message=f"review fixed\n{URL} the rows")
    assert push(repo, store, line(sha, repo.base)) == ("", 1)
    err = capsys.readouterr().err
    assert sha in err and "fixed" in err, err


def test_the_37fa812_message_is_refused(repo, store, capsys):
    sha = repo.commit({"a.txt": "x\n"}, message=MESSAGE_37FA812)
    assert push(repo, store, line(sha, repo.base)) == ("", 1)
    err = capsys.readouterr().err
    assert sha in err and "fixes" in err, err
    assert REFUSAL_WAY in err


def test_a_closing_hit_writes_no_event_and_no_pass(repo, store, capsys):
    # the store's closed guard set has no code for this check: the refusal is the stderr text
    sha = repo.commit({"a.txt": "x\n"}, message=f"fixes {URL}")
    assert push(repo, store, line(sha, repo.base)) == ("", 1)
    assert events(store) == []
    assert "event not written" not in capsys.readouterr().err


# --- references that pass --------------------------------------------------------------------

def test_refs_ticket_and_closing_words_without_a_reference_pass(repo, store, capsys):
    shas = [
        repo.commit({"a.txt": "1\n"}, message=f"work\n\nRefs {URL}"),
        repo.commit({"a.txt": "2\n"}, message=f"work\n\nTicket: {URL}"),
        repo.commit({"a.txt": "3\n"}, message="closed by a keyword, no reference"),
        repo.commit({"a.txt": "4\n"}, message="fixes nothing; closed-loop control"),
    ]
    assert push(repo, store, line(shas[-1], repo.base)) == ("", 0)
    assert capsys.readouterr().err == ""
    assert [row for row in events(store) if row[1] == "closing_keyword"] == []


# --- which commits are read: a new ref, a deleted ref ------------------------------------------

def test_a_new_branch_with_a_bad_commit_under_a_clean_tip_is_refused(repo, store, capsys):
    bad = repo.commit({"a.txt": "x\n"}, message=f"fixes {URL}")
    tip = repo.commit({"b.txt": "y\n"}, message="the tip is clean")
    assert push(repo, store, line(tip, ZEROS, ref="refs/heads/topic")) == ("", 1)
    assert bad in capsys.readouterr().err


def test_a_deleted_ref_is_skipped(repo, store, capsys):
    bad = repo.commit({"a.txt": "x\n"}, message=f"fixes {URL}")
    delete = f"(delete) {ZEROS} refs/heads/old {bad}\n"
    assert push(repo, store, delete) == ("", 0)
    assert capsys.readouterr().err == ""
    assert events(store) == []


# --- a hit always blocks; a failure of the check never wedges a push -------------------------

def test_a_hit_refuses_even_when_the_store_cannot_be_written(repo, capsys):
    class Broken:
        def writer(self):
            raise OSError("disk full")

    sha = repo.commit({"a.txt": "x\n"}, message=f"fixes {URL}")
    assert push(repo, Broken(), line(sha, repo.base)) == ("", 1)
    assert sha in capsys.readouterr().err


def test_a_hit_refuses_even_when_the_private_scan_crashes(repo, store, monkeypatch, capsys):
    def boom(*_):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(scanner, "scan_commits", boom)
    sha = repo.commit({"a.txt": "x\n"}, message=f"fixes {URL}")
    assert push(repo, store, line(sha, repo.base)) == ("", 1)
    assert sha in capsys.readouterr().err


def test_a_git_failure_of_the_check_fails_open_and_says_so(repo, store, monkeypatch):
    def failed(*_):
        raise scanner.ScanError("git_failed", "boom")

    monkeypatch.setattr(scanner, "messages_of_commits", failed)
    sha = repo.commit({"a.txt": "x\n"}, message=f"fixes {URL}")
    assert push(repo, store, line(sha, repo.base)) == ("", 0)
    [row] = store.query("SELECT kind, component, error_code, failed_open FROM events"
                        " WHERE kind = 'error.raised'").rows
    assert row == ("error.raised", "pre_push", "git_failed", 1)


def test_a_failed_check_does_not_skip_the_private_scan(repo, store, monkeypatch, capsys):
    def failed(*_):
        raise scanner.ScanError("git_failed", "boom")

    monkeypatch.setattr(scanner, "messages_of_commits", failed)
    sha = repo.commit({"a.txt": f"token {GITHUB_TOKEN}\n"}, message="clean message")
    assert push(repo, store, line(sha, repo.base)) == ("", 1)
    assert f"{sha[:12]} content a.txt:1 github_token" in capsys.readouterr().err


# --- real git through the tracked hook -----------------------------------------------------------

HOOK = ROOT / "hooks" / "pre-push"


@pytest.fixture
def hooked(tmp_path, repo):
    """`repo` with the tracked hook as its hooks folder and a bare remote; the hook runs under this
    interpreter on this checkout's source."""

    exe = sys.executable.replace("\\", "/")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    script = HOOK.read_text("utf-8").replace("python -m orch_it", f'"{exe}" -m orch_it')
    (hooks / "pre-push").write_bytes(script.encode("utf-8"))
    (hooks / "pre-push").chmod(0o755)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True,
                   creationflags=NO_WINDOW)
    repo.git("config", "core.hooksPath", str(hooks))
    repo.git("remote", "add", "origin", str(bare))
    repo.env = repo.env | {"PYTHONPATH": str(ROOT / "src"), "ORCH_IT_STORE": str(tmp_path / "hook-store"),
                           "PYTHONIOENCODING": "utf-8"}
    return repo


def test_a_real_push_of_a_new_branch_with_a_keyword_before_a_reference_is_refused(hooked):
    repo = hooked
    sha = repo.commit({"a.txt": "x\n"}, message=f"fixed\n{URL}")
    done = subprocess.run(["git", "-C", str(repo.path), "push", "origin", f"{sha}:refs/heads/topic"],
                          capture_output=True, env=repo.env, timeout=120, creationflags=NO_WINDOW)
    shown = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    assert done.returncode != 0, shown
    assert sha in shown and "fixed" in shown, shown
    assert REFUSAL_WAY in shown, shown
