"""The git pre-push hook (#395 decision 4, row 6): a push to a public repo is scanned for private
data and refused with file, line and kind (never the value); the same push to a private repo, a
clean push, a delete and a push with nothing new pass; a visibility nobody can read counts as
public; a hook that crashes lets the push through and says so, while a scan that finds something
refuses even when the store cannot be written.

Every private value is put together at run time from parts, so none stands in a committed file
(this repository's own pull requests are scanned). The visibility is injected: no test asks GitHub.
Two tests push with real git through the tracked `hooks/pre-push` file."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from orch_it.events.store import Store
from orch_it.guards import prepush, scanner
from orch_it.testruns import github
from scan_setup import invented_setup, with_values  # noqa: F401  (invented_setup: an autouse fixture)
from scan_repos import (ANTHROPIC_KEY, AWS_KEY, EMAIL, GITHUB_FINE_TOKEN, GITHUB_TOKEN, MAC_HOME, MSYS_HOME,
                        NO_WINDOW, OPENAI_KEY, PRIVATE_KEY, SOMEONE_HOME, WIN_HOME, WIN_TAIL, Repo,
                        default_host, invented_host, make_template)

ROOT = Path(__file__).resolve().parent.parent
ZEROS = "0" * 40
HOME_SPELLINGS = {
    "windows_back": WIN_HOME,
    "windows_forward": WIN_HOME.replace("\\", "/"),
    "msys": MSYS_HOME,
    "wsl": "/mnt/c/" + WIN_TAIL,
    "linux": SOMEONE_HOME,
    "mac": MAC_HOME,
    "root": "/ro" + "ot",
}
MACHINE_HOME = "D:" + "\\bo" + "b"
MACHINE_HOST = invented_host()
MACHINE_WORD = "Blue" + "Heron"
DEFAULT_HOST = default_host("WIN", 12)
# what the hook reads off the machine, put together by hand so no test depends on this one
PATTERNS = with_values(home=MACHINE_HOME, host=MACHINE_HOST, words=(MACHINE_WORD,))

# kind -> the value, for the places a value can be put (content and message take any; a path
# takes what a file name can hold on every OS)
IN_CONTENT = {
    **{f"home_path:{name}": ("home_path", value) for name, value in HOME_SPELLINGS.items()},
    "home:machine": ("home", MACHINE_HOME + "\\work"),
    "host:machine": ("host", MACHINE_HOST),
    "host_name:default": ("host_name", DEFAULT_HOST),
    "github_token": ("github_token", GITHUB_TOKEN),
    "github_token:fine": ("github_token", GITHUB_FINE_TOKEN),
    "anthropic_key": ("anthropic_key", ANTHROPIC_KEY),
    "openai_key": ("openai_key", OPENAI_KEY),
    "aws_key": ("aws_key", AWS_KEY),
    "private_key": ("private_key", PRIVATE_KEY),
    "email": ("email", EMAIL),
    "private_word": ("private_word", MACHINE_WORD),
}
IN_PATH = {name: case for name, case in IN_CONTENT.items() if not name.startswith(("home_path", "home:"))}


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text('{"repo": "acme-x/widgets-y"}', "utf-8")  # the repo the events are written under
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")


@pytest.fixture(scope="session")
def base_template(tmp_path_factory):
    """A repository with the first commit, built once per worker; every test copies it."""
    return make_template(tmp_path_factory.mktemp("template") / "work", {"README.md": "clean\n"},
                         message="the first work")


@pytest.fixture
def repo(tmp_path, base_template):
    path, base = base_template
    work = Repo(tmp_path / "work", template=path)
    work.base = base
    return work


def line(local, remote=ZEROS, ref="refs/heads/main"):
    return f"{ref} {local} {ref} {remote}\n"


def push(repo, store, stdin, *, visibility="public", url="https://example.invalid/o/n.git"):
    seen = []

    def reader(remote_url):
        seen.append(remote_url)
        if isinstance(visibility, Exception):
            raise visibility
        return visibility

    result = prepush.run_pre_push(stdin, store, remote_url=url, remote_name="origin",
                                  work_tree=repo.path, visibility=reader, patterns=PATTERNS)
    return result, seen


def events(store):
    return store.query("SELECT * FROM events ORDER BY at").rows


def event_text(store):
    return json.dumps(events(store), default=str)


# --- row 6: each kind, in each place, to a public and to a private remote ----------------------

def test_a_push_of_each_kind_in_content_is_refused_to_a_public_repo_naming_file_line_and_kind(
        repo, store, capsys):
    cases = list(IN_CONTENT.items())
    shas = repo.commits_on(repo.base, [{"files": {"notes.txt": f"fine\nsee {value} here\n"},
                                        "message": f"add {name}"} for name, (_, value) in cases])
    for (name, (kind, value)), sha in zip(cases, shas):
        (text, code), _ = push(repo, store, line(sha, repo.base))
        err = capsys.readouterr().err
        assert (text, code) == ("", 1), name
        assert f"{sha[:12]} content notes.txt:2 {kind}{scanner.cited(kind)}\n" in err, (name, err)
        assert value not in err, name
    assert value not in event_text(store)
    assert MACHINE_HOME not in event_text(store)


def test_a_push_with_no_repo_known_is_still_decided_writes_no_event_and_says_so(repo, store, tmp_path, capsys):
    (tmp_path / "settings.json").write_text("{}", "utf-8")  # no setting `repo`; the work tree has no origin
    value = IN_CONTENT["email"][1]
    sha = repo.commits_on(repo.base, [{"files": {"notes.txt": f"fine\nsee {value} here\n"},
                                       "message": "add email"}])[0]
    (text, code), _ = push(repo, store, line(sha, repo.base))
    err = capsys.readouterr().err
    assert (text, code) == ("", 1)
    assert f"{sha[:12]} content notes.txt:2 email\n" in err
    assert "no repo is known" in err
    assert value not in err
    assert events(store) == []


def test_a_push_of_each_kind_in_a_file_path_is_refused_and_the_path_is_shown_without_it(
        repo, store, capsys):
    cases = list(IN_PATH.items())
    shas = repo.commits_on(repo.base, [{"files": {f"data/{value}": "fine\n"}, "message": f"add {name}"}
                                       for name, (_, value) in cases])
    for (name, (kind, value)), sha in zip(cases, shas):
        (_, code), _ = push(repo, store, line(sha, repo.base))
        err = capsys.readouterr().err
        assert code == 1, name
        assert f"{sha[:12]} path data/<{kind}>:- {kind}{scanner.cited(kind)}\n" in err, (name, err)
        assert value not in err, name
    assert value not in event_text(store)


def test_a_push_of_each_kind_in_a_commit_message_is_refused(repo, store, capsys):
    cases = list(IN_CONTENT.items())
    shas = repo.commits_on(repo.base, [{"files": {"a.txt": "fine\n"},
                                        "message": f"first line\nthen {value}"} for _, (_, value) in cases])
    for (name, (kind, value)), sha in zip(cases, shas):
        (_, code), _ = push(repo, store, line(sha, repo.base))
        err = capsys.readouterr().err
        assert code == 1, name
        assert f"{sha[:12]} message -:2 {kind}{scanner.cited(kind)}\n" in err, (name, err)
        assert value not in err, name
    assert value not in event_text(store)


def test_a_non_noreply_author_or_committer_email_is_refused(repo, store, capsys):
    for who in ("author", "committer"):
        sha = repo.commit({"a.txt": "fine\n"}, **{who: EMAIL})
        (_, code), _ = push(repo, store, line(sha, repo.base))
        err = capsys.readouterr().err
        assert code == 1, who
        assert f"{sha[:12]} {who} -:- email\n" in err, (who, err)
        assert EMAIL not in err
        repo.git("reset", "-q", "--hard", repo.base)
    assert EMAIL not in event_text(store)


def test_the_same_pushes_to_a_private_repo_pass_and_say_nothing(repo, store, capsys):
    cases = [(visibility, name, value) for visibility in ("private", "internal")
             for name, (_, value) in IN_CONTENT.items() if not name.startswith(("home:", "host:"))]
    shas = repo.commits_on(repo.base, [{"files": {"notes.txt": f"see {value}\n"},
                                        "message": f"add {name} {value}"} for _, name, value in cases])
    for (visibility, name, _), sha in zip(cases, shas):
        (text, code), _ = push(repo, store, line(sha, repo.base), visibility=visibility)
        assert (text, code) == ("", 0), (visibility, name)
        assert capsys.readouterr().err == ""
    bad_author = repo.commit({"a.txt": "x\n"}, author=EMAIL)
    assert push(repo, store, line(bad_author, repo.base), visibility="private")[0] == ("", 0)
    # the kinds of #413 run on every push, so a clean scan of a private remote writes its pass; never a deny
    assert "deny" not in event_text(store)


def test_a_clean_push_to_a_public_repo_passes_and_writes_one_pass(repo, store, capsys):
    sha = repo.commit({"notes.txt": "fine\nuses: actions/checkout@v4\n"}, message="clean work")
    (text, code), seen = push(repo, store, line(sha, repo.base))
    assert (text, code) == ("", 0)
    assert capsys.readouterr().err == ""
    assert seen == ["https://example.invalid/o/n.git"]
    [row] = store.query("SELECT kind, guard, verdict, rule_id, path, repo, role FROM events").rows
    assert row == ("guard.fired", "private_data_public", "pass", "clean", None,
                   "acme-x/widgets-y", "hook")


@pytest.mark.parametrize("unreadable", ["unknown", "", "mystery", None, 7,
                                        github.GithubError("gh_timeout", "api"), RuntimeError("x")])
def test_a_visibility_nobody_can_read_counts_as_public(repo, store, capsys, unreadable):
    sha = repo.commit({"notes.txt": f"token {GITHUB_TOKEN}\n"})
    (_, code), _ = push(repo, store, line(sha, repo.base), visibility=unreadable)
    assert code == 1
    assert f"{sha[:12]} content notes.txt:1 github_token\n" in capsys.readouterr().err
    clean = repo.commit({"fine.txt": "ok\n"}, remove=["notes.txt"])
    assert push(repo, store, line(clean, sha), visibility=unreadable)[0] == ("", 0)


# --- which commits are scanned -----------------------------------------------------------------

def test_only_the_commits_the_remote_lacks_are_scanned(repo, store):
    leaked = repo.commit({"old.txt": f"token {GITHUB_TOKEN}\n"}, message="already on the remote")
    clean = repo.commit({"fine.txt": "ok\n"}, message="new and clean")
    assert push(repo, store, line(clean, leaked))[0] == ("", 0)
    worse = repo.commit({"new.txt": f"home {HOME_SPELLINGS['linux']}\n"}, message="new and leaking")
    (_, code), _ = push(repo, store, line(worse, leaked))
    assert code == 1


def test_a_new_ref_scans_every_commit_reachable_from_it(repo, store, capsys):
    repo.commit({"old.txt": f"token {GITHUB_TOKEN}\n"}, message="deep in the history")
    tip = repo.commit({"fine.txt": "ok\n"}, message="the tip is clean")
    (_, code), _ = push(repo, store, line(tip, ZEROS, ref="refs/heads/topic"))
    assert code == 1
    assert "content old.txt:1 github_token" in capsys.readouterr().err


def test_a_new_ref_skips_what_the_remote_tracking_refs_already_hold(repo, store):
    leaked = repo.commit({"old.txt": f"token {GITHUB_TOKEN}\n"})
    repo.git("update-ref", "refs/remotes/origin/main", leaked)
    tip = repo.commit({"fine.txt": "ok\n"})
    assert push(repo, store, line(tip, ZEROS, ref="refs/heads/topic"))[0] == ("", 0)


def test_a_remote_sha_this_clone_does_not_hold_scans_what_the_remote_tracking_refs_lack(repo, store):
    tip = repo.commit({"fine.txt": "ok\n"})
    assert push(repo, store, line(tip, "1" * 40))[0] == ("", 0)
    leaking = repo.commit({"new.txt": f"token {GITHUB_TOKEN}\n"})
    assert push(repo, store, line(leaking, "1" * 40))[0][1] == 1


def test_a_delete_push_and_a_push_with_nothing_new_pass_without_asking_github(repo, store):
    sha = repo.commit({"a.txt": f"token {GITHUB_TOKEN}\n"})
    delete = f"(delete) {ZEROS} refs/heads/old {sha}\n"
    (result, seen) = push(repo, store, delete)
    assert (result, seen) == (("", 0), [])
    assert push(repo, store, line(sha, sha))[0] == ("", 0)
    assert push(repo, store, "")[0] == ("", 0)
    assert events(store) == []


def test_several_refs_are_each_scanned_and_one_leak_refuses_the_push(repo, store, capsys):
    clean = repo.commit({"fine.txt": "ok\n"})
    repo.git("checkout", "-q", "-b", "other", repo.base)
    leaking = repo.commit({"x.txt": f"mail {EMAIL}\n"})
    stdin = line(clean, repo.base) + line(leaking, repo.base, ref="refs/heads/other")
    (_, code), _ = push(repo, store, stdin)
    assert code == 1
    assert f"{leaking[:12]} content x.txt:1 email\n" in capsys.readouterr().err
    kinds = sorted(r[0] for r in store.query("SELECT verdict FROM events").rows)
    assert kinds == ["deny"]


# --- the events --------------------------------------------------------------------------------

def test_each_hit_is_one_guard_fired_deny_with_the_kind_and_the_place_and_never_the_value(repo, store):
    sha = repo.commit({"notes.txt": f"fine\n{GITHUB_TOKEN}\nhome {HOME_SPELLINGS['linux']}/x\n"},
                      message=f"msg {AWS_KEY}")
    push(repo, store, line(sha, repo.base))
    rows = store.query("SELECT guard, verdict, rule_id, segment, path FROM events"
                       " WHERE kind = 'guard.fired' ORDER BY segment").rows
    assert sorted(rows) == sorted([
        ("private_data_public", "deny", "aws_key", f"{sha[:12]} message -:1", None),
        ("private_data_public", "deny", "github_token", f"{sha[:12]} content notes.txt:2", "notes.txt:2"),
        ("private_data_public", "deny", "home_path", f"{sha[:12]} content notes.txt:3", "notes.txt:3"),
    ])
    for value in (GITHUB_TOKEN, AWS_KEY, HOME_SPELLINGS["linux"]):
        assert value not in event_text(store)
    [full] = store.query("SELECT * FROM events WHERE rule_id = 'aws_key'").rows
    assert full  # the writer accepted the whole envelope


def test_a_segment_and_a_path_stay_within_their_bounds(repo, store, monkeypatch):
    long_hit = scanner.Hit("github_token", "content", "a" * 40, "d/" * 300 + "<github_token>", 7)
    monkeypatch.setattr(scanner, "scan_commits", lambda *_: [long_hit])
    sha = repo.commit({"a.txt": "x\n"})
    assert push(repo, store, line(sha, repo.base))[0] == ("", 1)
    [(segment, path)] = store.query("SELECT segment, path FROM events").rows
    assert 100 < len(segment) <= 120 and 100 < len(path) <= 200


# --- it never raises; a crash lets the push through --------------------------------------------

def test_a_scan_that_crashes_lets_the_push_through_and_writes_error_raised_failed_open(
        repo, store, monkeypatch, capsys):
    def boom(*_):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(scanner, "scan_commits", boom)
    sha = repo.commit({"a.txt": "x\n"})
    assert push(repo, store, line(sha, repo.base))[0] == ("", 0)
    [row] = store.query("SELECT kind, component, error_code, failed_open FROM events").rows
    assert row == ("error.raised", "pre_push", "RuntimeError", 1)


def test_a_git_that_cannot_be_read_is_a_crash_not_a_refusal(repo, store):
    bad = line("f" * 40, "e" * 40)  # shas that name no object here
    assert push(repo, store, bad)[0] == ("", 0)
    [row] = store.query("SELECT kind, component, failed_open FROM events").rows
    assert row[:2] == ("error.raised", "pre_push")


def test_an_unreadable_private_words_file_is_a_crash_that_fails_open(repo, store, tmp_path, monkeypatch):
    broken = tmp_path / "words.txt"
    broken.write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\x28")
    (tmp_path / "settings.json").write_text(json.dumps(
        {"private_words_file": str(broken), "repo": "acme-x/widgets-y"}))
    sha = repo.commit({"a.txt": f"token {GITHUB_TOKEN}\n"})
    result = prepush.run_pre_push(line(sha, repo.base), store, remote_url="x", remote_name="origin",
                                  work_tree=repo.path, visibility=lambda _: "public")
    assert result == ("", 0)
    [row] = store.query("SELECT error_code, failed_open FROM events").rows
    assert row == ("words_unreadable", 1)


def test_a_hit_refuses_even_when_the_store_cannot_be_written(repo, capsys):
    class Broken:
        def writer(self):
            raise OSError("disk full")

    sha = repo.commit({"a.txt": f"token {GITHUB_TOKEN}\n"})
    (_, code), _ = push(repo, Broken(), line(sha, repo.base))
    assert code == 1
    assert "github_token" in capsys.readouterr().err


def test_a_crash_with_a_store_that_cannot_be_written_still_lets_the_push_through(repo):
    class Broken:
        def writer(self):
            raise OSError("disk full")

    assert push(repo, Broken(), line("f" * 40, "e" * 40))[0] == ("", 0)


def test_input_that_is_not_a_push_is_let_through(repo, store):
    for stdin in ("garbage\n", "a b\n", "\n\n", "refs/heads/x zz refs/heads/x yy\n", "\x00\xff"):
        assert push(repo, store, stdin)[0] == ("", 0), stdin


# --- the real visibility reader ----------------------------------------------------------------

@pytest.mark.parametrize("url, repo_name", [
    ("https://github.com/Own-er/na.me.git", "Own-er/na.me"),
    ("https://github.com/Owner/name", "Owner/name"),
    ("https://user:secret" + "@" + "github.com/Owner/name.git", "Owner/name"),
    ("git@github.com:Owner/name.git", "Owner/name"),
    ("ssh://git@github.com/Owner/name.git", "Owner/name"),
    ("ssh://git@github.com:22/Owner/name.git", "Owner/name"),
    ("https://github.com/Owner/name/", "Owner/name"),
])
def test_the_github_repo_is_read_from_the_remote_url(url, repo_name):
    assert prepush.github_repo_of(url) == repo_name


@pytest.mark.parametrize("url", ["", "/tmp/bare.git", "C:/repos/bare.git", "https://gitlab.com/o/n.git",
                                 "https://github.com/onlyowner", "https://example.com/github.com/o/n",
                                 "https://github.com/o/n/extra"])
def test_a_remote_that_is_no_github_repo_has_no_name(url):
    assert prepush.github_repo_of(url) is None


def test_the_reader_asks_the_api_for_the_visibility_of_the_repo(monkeypatch):
    calls = []

    def fake(args):
        calls.append(args)
        return "private\n"

    monkeypatch.setattr(github, "run_gh", fake)
    assert prepush.visibility_of("git@github.com:Owner/name.git") == "private"
    assert calls == [["api", "repos/Owner/name", "--jq", ".visibility"]]


@pytest.mark.parametrize("answer, expected", [("public\n", "public"), ("PRIVATE", "private"),
                                              ("internal", "internal"), ("", "unknown"),
                                              ("null", "unknown"), ("who knows", "unknown")])
def test_the_reader_names_only_the_three_visibilities(monkeypatch, answer, expected):
    monkeypatch.setattr(github, "run_gh", lambda args: answer)
    assert prepush.visibility_of("https://github.com/o/n") == expected


def test_the_reader_says_unknown_when_gh_fails_and_never_calls_it_for_a_local_remote(monkeypatch):
    def fail(args):
        raise github.GithubError("gh_failed", "no auth")

    monkeypatch.setattr(github, "run_gh", fail)
    assert prepush.visibility_of("https://github.com/o/n") == "unknown"
    monkeypatch.setattr(github, "run_gh", lambda args: pytest.fail("asked gh about a local path"))
    assert prepush.visibility_of("/tmp/bare.git") == "unknown"


# --- the tracked hook file, and real git pushes through it ----------------------------------------

HOOK = ROOT / "hooks" / "pre-push"


def test_the_tracked_hook_is_a_small_lf_sh_script_that_runs_the_entry_point_and_is_executable():
    raw = HOOK.read_bytes()
    assert b"\r" not in raw
    lines = raw.decode("utf-8").splitlines()
    assert lines[0] == "#!/bin/sh"
    # runs the entry point, and only the guard's own refusal (exit 1) blocks — any other
    # nonzero code is a launcher failure and fails open (decision 7)
    assert 'sh "$(dirname "$0")/orch-it" -m orch_it hook pre-push "$@"' in lines
    assert '[ "$code" = "1" ] && exit 1' in lines
    assert "exit 0" in lines
    assert len(lines) <= 16
    mode = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "--", "hooks/pre-push"],
                          capture_output=True, text=True, creationflags=NO_WINDOW).stdout.split()[0]
    assert mode == "100755"
    if os.name == "posix":
        assert HOOK.stat().st_mode & stat.S_IXUSR


@pytest.fixture
def hooked(tmp_path, repo):
    """`repo` with the tracked hook installed as its hooks folder (run by this interpreter, on this
    checkout's source) and a bare remote that no one can ask GitHub about."""

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
    return repo, bare


def test_a_real_push_of_a_leak_is_refused_by_the_tracked_hook_and_a_clean_one_goes_through(hooked):
    repo, bare = hooked
    repo.git("push", "origin", "main")
    assert repo.git("rev-parse", "HEAD") == subprocess.run(
        ["git", "-C", str(bare), "rev-parse", "main"], capture_output=True, text=True,
        creationflags=NO_WINDOW).stdout.strip()
    leaking = repo.commit({"notes.txt": f"fine\n{GITHUB_TOKEN}\n"}, message="oops")
    done = subprocess.run(["git", "-C", str(repo.path), "push", "origin", "main"], capture_output=True,
                          env=repo.env, timeout=120, creationflags=NO_WINDOW)
    shown = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    assert done.returncode != 0, shown
    assert f"{leaking[:12]} content notes.txt:2 github_token" in shown
    assert GITHUB_TOKEN not in shown
    refs = subprocess.run(["git", "-C", str(bare), "rev-parse", "main"], capture_output=True, text=True,
                          creationflags=NO_WINDOW).stdout.strip()
    assert refs != leaking
    stored = Store.open(Path(repo.env["ORCH_IT_STORE"]))
    assert GITHUB_TOKEN not in json.dumps(stored.query("SELECT * FROM events").rows, default=str)
    [(denies,)] = stored.query("SELECT count(*) FROM events WHERE verdict = 'deny'").rows
    assert denies == 1


def test_a_real_push_of_a_new_branch_is_scanned_from_the_start(hooked):
    repo, bare = hooked
    repo.git("checkout", "-q", "-b", "topic")
    repo.commit({"notes.txt": f"home {HOME_SPELLINGS['linux']}\n"}, message="topic work")
    done = subprocess.run(["git", "-C", str(repo.path), "push", "origin", "topic"], capture_output=True,
                          env=repo.env, timeout=120, creationflags=NO_WINDOW)
    assert done.returncode != 0
    assert b"home_path" in done.stderr + done.stdout


@pytest.mark.parametrize("fake_code,expected", [(1, 1), (0, 0), (2, 0), (127, 0)])
def test_the_pre_push_hook_script_fails_open_on_a_launcher_failure(tmp_path, fake_code, expected):
    # decision 7: only the guard's own refusal (exit 1) blocks; a launcher failure (python
    # missing, an argparse error, exit 2/127) lets the push through rather than wedging it
    import shutil as _sh
    import subprocess as _sp
    sh = _sh.which("sh") or _sh.which("bash")
    if sh is None:
        pytest.skip("no POSIX sh to run the hook script")
    script = Path(__file__).resolve().parents[1] / "hooks" / "pre-push"
    fake = tmp_path / "fake-interpreter"
    # the probe `-c "import orch_it"` exits 0 so the shim picks this fake; the run then exits with fake_code
    fake.write_text(f'#!/bin/sh\ncase "$*" in\n  *"import orch_it"*) exit 0 ;;\nesac\nexit {fake_code}\n')
    fake.chmod(0o755)
    env = dict(os.environ)
    env["ORCH_IT_PYTHON"] = fake.as_posix()  # the shim runs this interpreter, whatever PATH holds
    done = _sp.run([sh, str(script), "origin", "https://x/y.git"], input="", text=True,
                   env=env, capture_output=True, timeout=30)
    assert done.returncode == expected, (fake_code, done.returncode, done.stderr)
