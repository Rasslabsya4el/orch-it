"""The git pre-push hook's closing check (#422, row 3): a commit a push sends to GitHub whose message
holds a closing keyword before a ticket reference is refused, whatever the remote's visibility; a
`Refs <URL>` or `Ticket: <URL>` line, or a closing word with no reference, passes. The check reads
the commits the remote lacks (the same set the private-data scan reads), a new ref, a tag and a
non-fast-forward push included, and skips a deleted ref. The refusal names the line of each keyword.
A git failure of the check lets the push through for the part it could not read, keeps the hits it
read before the failure, and never stops the private-data scan; a hit always refuses.

The messages are written with the real git of a temporary repository. The visibility is injected:
no test asks GitHub. One test pushes with real git through the tracked `hooks/pre-push` file."""

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
OTHER_URL = "https://github.com/example-owner/example-repo/issues/12"
# the one message the check has to refuse in this repo's history (`git show -s --format=%B 37fa812`),
# with its owner and repo replaced by invented names
MESSAGE_37FA812 = (
    "actions_enabled: only the workflow token's 403 reads as on; review fixes\n\n"
    "https://github.com/example-owner/example-repo/issues/411 unit 9 review: a rate-limit 403 and a person's\n"
    "login without admin keep raising; two tests for them; docstring says exactly this; the ticket URL and the\n"
    "duplicate test left the permissions test file.")
# what the hook reads off the machine is not under test here: the closing check needs none of it
PATTERNS = Patterns()
REFUSAL_WAY = "write `Refs <URL>` or `Ticket: <URL>`"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text('{"repo": "acme-x/widgets-y"}', "utf-8")  # the repo the events are written under
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")


@pytest.fixture
def repo(tmp_path):
    work = Repo(tmp_path / "work")
    work.base = work.commit({"README.md": "clean\n"}, message="the first work")
    return work


@pytest.fixture
def small_batches(monkeypatch):
    """One commit per `git log` batch, so a failure can fall between two batches."""
    monkeypatch.setattr(scanner, "_BATCH", 1)


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


def test_the_refusal_names_the_line_of_each_keyword(repo, store, capsys):
    sha = repo.commit({"a.txt": "x\n"},
                      message=f"first line\nsecond line\nfixed\n{URL}\nresolves {OTHER_URL}")
    assert push(repo, store, line(sha, repo.base)) == ("", 1)
    err = capsys.readouterr().err
    assert f'{sha}, line 3, "fixed", fixed {URL}' in err, err
    assert f'{sha}, line 5, "resolves {OTHER_URL}", resolves {OTHER_URL}' in err, err


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


# --- which commits are read: a new ref, a deleted ref, a tag, a non-fast-forward ----------------

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


def test_a_new_tag_is_read_like_a_branch(repo, store, capsys):
    bad = repo.commit({"a.txt": "x\n"}, message=f"fixes {URL}")
    assert push(repo, store, line(bad, ZEROS, ref="refs/tags/v1")) == ("", 1)
    assert bad in capsys.readouterr().err


def test_an_annotated_tag_is_read_through_to_its_commits(repo, store, capsys):
    bad = repo.commit({"a.txt": "x\n"}, message=f"fixes {URL}")
    repo.git("tag", "-a", "v2", "-m", "release notes", bad)
    tag_object = repo.git("rev-parse", "refs/tags/v2")
    assert tag_object != bad
    assert push(repo, store, line(tag_object, ZEROS, ref="refs/tags/v2")) == ("", 1)
    assert bad in capsys.readouterr().err


def test_a_clean_tag_passes(repo, store, capsys):
    clean = repo.commit({"a.txt": "x\n"}, message="a clean release")
    assert push(repo, store, line(clean, ZEROS, ref="refs/tags/v3")) == ("", 0)
    assert capsys.readouterr().err == ""


def test_a_non_fast_forward_push_reads_only_what_the_remote_lacks(repo, store, capsys):
    on_remote = repo.commit({"r.txt": "r\n"}, message=f"fixes {URL}")
    repo.git("checkout", "-q", "-b", "other", repo.base)
    local = repo.commit({"l.txt": "l\n"}, message="clean, not a fast-forward")
    assert push(repo, store, line(local, on_remote)) == ("", 0)
    assert capsys.readouterr().err == ""
    bad = repo.commit({"l.txt": "2\n"}, message=f"resolves {URL}")
    assert push(repo, store, line(bad, on_remote)) == ("", 1)
    assert bad in capsys.readouterr().err


def test_a_remote_sha_the_clone_lacks_reads_what_no_remote_ref_holds(repo, store, capsys):
    bad_on_remote = repo.commit({"a.txt": "1\n"}, message=f"fixes {URL}")
    repo.git("update-ref", "refs/remotes/origin/main", bad_on_remote)
    tip = repo.commit({"b.txt": "2\n"}, message="clean tip")
    assert push(repo, store, line(tip, "1" * 40)) == ("", 0)
    assert capsys.readouterr().err == ""
    leaking = repo.commit({"c.txt": "3\n"}, message=f"resolves {URL}")
    assert push(repo, store, line(leaking, "1" * 40)) == ("", 1)
    assert leaking in capsys.readouterr().err


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


def test_hits_in_every_batch_are_all_reported(repo, store, capsys, small_batches):
    first = repo.commit({"a.txt": "1\n"}, message=f"fixes {URL}")
    repo.commit({"a.txt": "2\n"}, message="clean middle")
    last = repo.commit({"a.txt": "3\n"}, message=f"resolves {URL}")
    assert push(repo, store, line(last, repo.base)) == ("", 1)
    err = capsys.readouterr().err
    assert f"{first}, line 1, " in err and f"{last}, line 1, " in err, err


def test_a_late_git_failure_keeps_the_hits_read_before_it(repo, store, capsys, monkeypatch,
                                                          small_batches):
    real = scanner._git_output
    reads = []

    def fail_the_second_read(work_tree, *args):
        if "--no-walk=unsorted" in args:
            reads.append(args)
            if len(reads) == 2:
                raise scanner.ScanError("git_failed", "boom")
        return real(work_tree, *args)

    monkeypatch.setattr(scanner, "_git_output", fail_the_second_read)
    first = repo.commit({"a.txt": "1\n"}, message=f"fixes {URL}")
    repo.commit({"a.txt": "2\n"}, message="clean middle")
    unread = repo.commit({"a.txt": "3\n"}, message=f"resolves {URL}")
    assert push(repo, store, line(unread, repo.base)) == ("", 1)
    err = capsys.readouterr().err
    assert f"{first}, line 1, " in err, err
    assert unread not in err
    assert len(reads) == 2
    rows = store.query("SELECT error_code, failed_open FROM events WHERE kind = 'error.raised'").rows
    assert rows == [("git_failed", 1)]


def test_a_late_git_failure_before_any_hit_fails_open_for_the_unread_part(
        repo, store, capsys, monkeypatch, small_batches):
    real = scanner._git_output
    reads = []

    def fail_the_third_read(work_tree, *args):
        if "--no-walk=unsorted" in args:
            reads.append(args)
            if len(reads) == 3:
                raise scanner.ScanError("git_failed", "boom")
        return real(work_tree, *args)

    monkeypatch.setattr(scanner, "_git_output", fail_the_third_read)
    repo.commit({"a.txt": "1\n"}, message="clean first")
    repo.commit({"a.txt": "2\n"}, message="clean second")
    unread_tip = repo.commit({"a.txt": "3\n"}, message=f"fixes {URL}")
    assert push(repo, store, line(unread_tip, repo.base)) == ("", 0)
    assert capsys.readouterr().err == ""
    assert len(reads) == 3
    rows = store.query("SELECT error_code, failed_open FROM events WHERE kind = 'error.raised'").rows
    assert rows == [("git_failed", 1)]


# --- real git through the tracked hook -----------------------------------------------------------

HOOK = ROOT / "hooks" / "pre-push"


@pytest.fixture
def hooked(tmp_path, repo):
    """`repo` with the tracked hook as its hooks folder and a bare remote; the hook runs under this
    interpreter on this checkout's source."""

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name in ("pre-push", "orch-it"):  # the hook and the interpreter shim it calls, beside it
        (hooks / name).write_bytes((ROOT / "hooks" / name).read_bytes())
    (hooks / "pre-push").chmod(0o755)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True,
                   creationflags=NO_WINDOW)
    repo.git("config", "core.hooksPath", str(hooks))
    repo.git("remote", "add", "origin", str(bare))
    repo.env = repo.env | {"PYTHONPATH": str(ROOT / "src"), "ORCH_IT_STORE": str(tmp_path / "hook-store"),
                           "PYTHONIOENCODING": "utf-8", "ORCH_IT_PYTHON": sys.executable}
    return repo


def test_a_real_push_of_a_new_branch_with_a_keyword_before_a_reference_is_refused(hooked):
    repo = hooked
    sha = repo.commit({"a.txt": "x\n"}, message=f"fixed\n{URL}")
    done = subprocess.run(["git", "-C", str(repo.path), "push", "origin", f"{sha}:refs/heads/topic"],
                          capture_output=True, env=repo.env, timeout=120, creationflags=NO_WINDOW)
    shown = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    assert done.returncode != 0, shown
    assert f"{sha}, line 1, \"fixed\", fixed {URL}" in shown, shown
    assert REFUSAL_WAY in shown, shown
