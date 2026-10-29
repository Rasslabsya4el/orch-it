"""The private-data scanner (#395 decision 4; rows 6, 7 and 8): the kinds it finds in one line of
text, in the lines a commit adds, in its paths, its message and its author and committer, and in
the files of a tree; and the command `guards scan` that carries it to the hook, the job and the
history run.

Every secret-shaped value, home path and non-noreply address below is put together at run time
from parts, so no such value stands in a committed file: this repository's own pull requests are
scanned by the step these tests cover. A hit never carries the value it found; each test of a
kind also checks that the value is in no text the scanner or the command produces."""

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import pytest

from orch_it import settings
from orch_it.cli import main
from orch_it.guards import scanner
from orch_it.guards.scanner import KINDS, Hit, Patterns, ScanError
from orch_it.testruns import github
from scan_setup import invented_setup, with_values  # noqa: F401  (invented_setup: an autouse fixture)
from scan_repos import (ANTHROPIC_KEY, AWS_KEY, EMAIL, GITHUB_FINE_TOKEN, GITHUB_TOKEN, INVENTED_USER,
                        MAC_HOME, MSYS_HOME, NO_WINDOW, NOREPLY, OPENAI_KEY, PRIVATE_KEY, SOMEONE_HOME,
                        WIN_HOME, WIN_TAIL, Repo, default_host, invented_host, make_template)

# kind -> a line holding one value of it, the value
LINES = {
    "home_path": (f"cd {SOMEONE_HOME}/work", SOMEONE_HOME),
    "github_token": (f"token = {GITHUB_TOKEN}", GITHUB_TOKEN),
    "anthropic_key": (f"key: {ANTHROPIC_KEY}", ANTHROPIC_KEY),
    "openai_key": (f"export KEY={OPENAI_KEY}", OPENAI_KEY),
    "aws_key": (f"id {AWS_KEY} ok", AWS_KEY),
    "private_key": (PRIVATE_KEY, PRIVATE_KEY),
    "email": (f"mail {EMAIL}", EMAIL),
}


HOST = invented_host()  # a generated host name, never a real one
HOST_LONG = HOST + ".dept.example"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))


def kinds(text: str, patterns: Patterns | None = None) -> list[str]:
    return (patterns or Patterns()).find(text)


# --- the kinds in one line ------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(LINES))
def test_each_kind_is_found_in_a_line(kind):
    line, _ = LINES[kind]
    assert kinds(line) == [kind]


def test_the_kinds_are_the_nine_of_the_decision():
    assert KINDS == ("home_path", "host_name", "github_token", "anthropic_key", "openai_key",
                     "aws_key", "private_key", "email", "private_word")


def test_a_line_with_several_kinds_lists_each_once_in_the_order_of_the_kinds():
    line = f"{EMAIL} {EMAIL} {AWS_KEY} {SOMEONE_HOME}/a {SOMEONE_HOME}/b {GITHUB_TOKEN}"
    assert kinds(line) == ["home_path", "github_token", "aws_key", "email"]


def test_the_fine_grained_github_token_shape_is_a_github_token():
    assert kinds(f"t {GITHUB_FINE_TOKEN}") == ["github_token"]


@pytest.mark.parametrize("prefix", ["ghp_", "gho_", "ghu_", "ghs_", "ghr_"])
def test_every_github_token_prefix_is_found(prefix):
    assert kinds(f"x {prefix}{'a1' * 18}") == ["github_token"]


@pytest.mark.parametrize("text", [
    "gh" + "p_" + "a" * 35,                       # too short
    "xgh" + "p_" + "a" * 36,                      # no left boundary
    "sk" + "-" + "a" * 19,                        # too short for a key
    "task-" + "a" * 30,                           # a word that ends in sk
    "desk-" + "a" * 30,
    "risk-assessment-and-mitigation-plan",
    "AK" + "IA" + "A" * 15,                       # too short
    "AK" + "IA" + "A" * 17,                       # no right boundary
    "xAK" + "IA" + "A" * 16,
    "sk" + "-ant-" + "a" * 15,                    # too short
])
def test_secret_look_alikes_are_no_hit(text):
    assert kinds(text) == []


def test_an_anthropic_key_is_not_also_an_openai_key():
    assert kinds(ANTHROPIC_KEY) == ["anthropic_key"]


@pytest.mark.parametrize("text", [
    "-----" + "BEGIN PRIVATE" + " KEY-----",
    "-----" + "BEGIN OPENSSH PRIVATE" + " KEY-----",
    "-----" + "BEGIN ENCRYPTED PRIVATE" + " KEY-----",
    "-----" + "BEGIN PGP PRIVATE" + " KEY BLOCK-----",
])
def test_private_key_headers_of_every_form_are_found(text):
    assert kinds(text) == ["private_key"]


def test_a_public_key_header_is_no_hit():
    assert kinds("-----" + "BEGIN PUBLIC" + " KEY-----") == []


# --- home paths -----------------------------------------------------------------------------

@pytest.mark.parametrize("home", [
    WIN_HOME,
    WIN_HOME.replace("C:", "d:").replace("Users", "users"),
    WIN_HOME.replace("\\", "/"),
    WIN_HOME.replace("\\", "\\\\"),
    MSYS_HOME,
    "/cygdrive/c/" + WIN_TAIL,
    "/mnt/c/" + WIN_TAIL,
    SOMEONE_HOME,
    MAC_HOME,
    "/ro" + "ot",
])
def test_every_spelling_of_a_home_path_is_found(home):
    for text in (home, f"in {home}/work", f'open("{home}/x")', f"p={home}"):
        assert kinds(text) == ["home_path"], text


@pytest.mark.parametrize("text", [
    "C:" + "\\Us" + "ers\\<name>\\x",
    "C:" + "\\Us" + "ers\\%USERNAME%\\x",
    "/ho" + "me/$USER/x",
    "/ho" + "me/<name>",
    "/Us" + "ers/",
    "src/root.py",
    "rooted",
    "/rooted/x",
    "/root.py",
    "see src/root/x",
])
def test_documentation_placeholders_and_look_alikes_are_no_home_path(text):
    assert kinds(text) == []


def test_the_root_folder_counts_only_at_the_start_of_a_path():
    root = "/ro" + "ot"
    assert kinds(f"cd {root}/x") == ["home_path"]
    assert kinds(root) == ["home_path"]
    assert kinds(f"a,{root}") == ["home_path"]
    assert kinds(f"[{root}]") == ["home_path"]
    assert kinds(f"a{root}") == []
    assert kinds(f"a-{root}") == []


def test_a_bare_root_folder_in_a_code_span_is_prose_but_a_path_in_one_is_not():
    root = "/ro" + "ot"
    assert kinds(f"the `{root}` folder") == []
    assert kinds(f"run `cd {root}/x` now") == ["home_path"]
    assert kinds(f"see `{root}/x`") == ["home_path"]
    assert kinds(f"see `{SOMEONE_HOME}/x`") == ["home_path"]


@pytest.mark.parametrize("text", [
    "app/ho" + "me/index.html",
    "x/Us" + "ers/y",
    "https://example.org/Us" + "ers/list",
    "/ho" + "me/runner/work/repo",
    "/ho" + "me/runneradmin/x",
    "C:" + "\\Us" + "ers\\Public\\x",
    "C:" + "\\Us" + "ers\\Default User\\x",
    "C:" + "\\Us" + "ers\\All Users\\x",
    "/Us" + "ers/Shared/x",
    "~/ho" + "me/x",
    "${HOME}/ho" + "me/x",
    "$(pwd)/ro" + "ot/x",
    "/mn" + "t/c/Us" + "ers/Public",
    "a/ro" + "ot/x",
])
def test_paths_that_only_look_like_a_home_folder_are_no_hit(text):
    assert kinds(text) == [], text


@pytest.mark.parametrize("text", [
    "/ho" + "me/bob",
    'File "/ho' + 'me/bob/x.py", line 3',
    '"/ho' + 'me/bob/x"',
    "<a href=/ho" + "me/bob>",
    "x>/ho" + "me/bob",
    "a;/Us" + "ers/bob/x",
    "file:///ho" + "me/bob/x",
    "C:" + "\\Us" + "ers\\Publicity\\x",
    "/ho" + "me/runners/x",
])
def test_a_home_folder_after_a_character_that_ends_a_name_is_found(text):
    assert kinds(text) == ["home_path"], text


@pytest.mark.parametrize("before", [">", ";", "<", "(", "[", ":", ",", "=", '"', "'", " ", "|"])
def test_the_root_folder_follows_any_character_that_is_not_part_of_a_path(before):
    assert kinds(f"x{before}" + "/ro" + "ot/y") == ["home_path"]


def test_a_value_that_overlaps_another_is_redacted_whole():
    text = "see " + WIN_HOME + "@" + "corp.example.io now"
    shown = Patterns().redact(text)
    assert shown == "see <home_path> now"
    assert "corp" not in shown and INVENTED_USER not in shown
    shown = Patterns().redact(GITHUB_TOKEN + "@" + "corp.example.io")
    assert "corp" not in shown and "ghp" not in shown


def test_asset_names_with_a_density_suffix_are_no_email_and_a_real_address_still_is():
    for name in ("logo@2x.png", "icon@3x.jpg", "a/b@2x.svg", "x@1.5x.webp", "s@2x.css", "d@2x.json"):
        assert kinds(name) == [], name
    assert kinds("a.b" + "@" + "corp.io") == ["email"]
    assert kinds("a.b" + "@" + "corp.io.png") == []


def test_the_errors_and_the_places_of_a_hit_are_closed_sets():
    assert scanner.SCAN_ERROR_CODES == ("words_unreadable", "git_failed", "bad_rev", "git_timeout")
    assert scanner.WHERES == ("content", "path", "message", "author", "committer")
    for code in scanner.SCAN_ERROR_CODES:
        assert ScanError(code, "detail").code == code
    with pytest.raises(ValueError):
        ScanError("made_up")


def test_the_machine_home_outside_the_usual_folders_is_found_in_every_spelling():
    patterns = with_values(home="D:" + "\\me")
    for spelling in ("D:" + "\\me\\x", "d:" + "/me/x", "D:" + "\\\\me\\\\x", "/d" + "/me/x",
                     "/cyg" + "drive/d/me", "/mn" + "t/d/me/x", "at D:" + "\\me."):
        assert kinds(spelling, patterns) == ["home"], spelling
    for other in ("D:" + "\\meal\\x", "D:" + "\\me-too", "E:" + "\\me\\x", "x/d/me/x"):
        assert kinds(other, patterns) == [], other


def test_a_posix_machine_home_is_matched_as_it_is():
    home = str(PurePosixPath("/data") / INVENTED_USER)
    patterns = with_values(home=home)
    assert kinds("ls " + home + "/x", patterns) == ["home"]
    assert kinds("ls " + home, patterns) == ["home"]
    assert kinds("ls " + home + "lse/x", patterns) == []


def test_the_machine_independent_patterns_know_no_machine_home():
    assert kinds("D:" + "\\me\\x") == []


# --- the host name --------------------------------------------------------------------------

def test_the_host_name_and_its_first_label_are_found_whole_word_in_any_case():
    patterns = with_values(host=HOST_LONG)
    for text in (f"on {HOST.upper()}", f"{HOST_LONG}:22", f"host={HOST};", f"({HOST.lower()})"):
        assert kinds(text, patterns) == ["host"], text
    for other in (HOST + "0", "x" + HOST, "testbox"):
        assert kinds(other, patterns) == [], other


# the default host names an OS gives a machine nobody named (#395 brain answer): found on any
# machine, so the job's machine-independent scan catches a leaked one. Made at run time, each of
# the shape the scanner's default-name pattern takes (WIN 8 to 15 characters, DESKTOP 7, LAPTOP 8).
DEFAULT_HOSTS = [default_host("WIN", 12), default_host("DESKTOP", 7), default_host("LAPTOP", 8)]


@pytest.mark.parametrize("name", DEFAULT_HOSTS)
def test_a_default_host_name_is_found_on_any_machine(name):
    for patterns in (Patterns(), Patterns.of_machine(), Patterns.of_run()):
        assert patterns.find(f"hostname={name} ok") == ["host_name"]
    assert Patterns().redact(f'hostname="{name}"') == 'hostname="<host_name>"'


_WIN = default_host("WIN", 12)


@pytest.mark.parametrize("text", [
    "WINDOWS", "WIN-abc", _WIN.lower(), "desktop", default_host("DESKTOP", 8), default_host("DESKTOP", 6),
    "MY-" + _WIN, _WIN + "-X", _WIN + "_1", "X" + _WIN,
    "WIN-SHORT", default_host("LAPTOP", 9),
])
def test_what_is_no_default_host_name_is_not_found(text):
    assert Patterns().find(text) == []


@pytest.mark.parametrize("host", ["localhost", "LocalHost", "ab", "", None])
def test_a_host_that_is_localhost_or_too_short_is_not_a_pattern(host):
    assert kinds("localhost ab here", with_values(host=host)) == []


# --- emails ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "pytest.mark@x",
    "uses: actions/checkout@v4",
    "uses: actions/setup-python@v5.1.0",
    "git@github.com:o/r.git",
    "noreply@anthropic.com",
    "No-Reply@somewhere.org",
    "1234+someone@users.noreply.github.com",
    "someone@users.noreply.github.com",
    "a@example.com",
    "a@mail.example.org",
    "a@EXAMPLE.NET",
    "a@host.example",
    "a@host.test",
    "a@host.invalid",
    "a@db.localhost",
    "@pytest.mark.slow",
    "a@",
    "@b.com",
])
def test_addresses_that_are_not_private_are_no_hit(text):
    assert kinds(text) == []


def test_an_address_is_a_hit_unless_it_is_allowed():
    allowed = Patterns(allowed_emails=[EMAIL.upper()])
    assert kinds(f"by {EMAIL}") == ["email"]
    assert kinds(f"by {EMAIL}", allowed) == []
    assert kinds("by " + "other" + "@" + "corp.io", allowed) == ["email"]


def test_an_address_in_a_domain_that_only_starts_like_a_reserved_one_is_a_hit():
    assert kinds("a" + "@" + "example.com.evil.io") == ["email"]
    assert kinds("a" + "@" + "myexample.com") == ["email"]
    assert kinds("a" + "@" + "notusers.noreply.github.com") == ["email"]


# --- private words --------------------------------------------------------------------------

def test_a_private_word_is_found_whole_word_in_any_case():
    patterns = Patterns(words=("Project" + "Nightowl", "red" + " fox"))
    assert kinds("see projectnightowl.md", patterns) == ["private_word"]
    assert kinds("the RED  FOX", patterns) == []
    assert kinds("the Red Fox ran", patterns) == ["private_word"]
    assert kinds("xprojectnightowl", patterns) == []
    assert kinds("projectnightowls", patterns) == []


def test_a_word_that_starts_or_ends_in_a_symbol_needs_no_boundary_there():
    patterns = Patterns(words=("#" + "secret!",))
    assert kinds("a#secret!b", patterns) == ["private_word"]


# --- the machine's own patterns -------------------------------------------------------------

def test_of_machine_measures_the_home_the_host_and_the_words_file(tmp_path, monkeypatch):
    home = tmp_path / "somebody"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(socket, "gethostname", lambda: HOST_LONG)
    # a GitHub Actions job adds the runner's own home, temp parent and computer name: this test measures a machine
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    words = tmp_path / "words.txt"
    words.write_text("# my words\n\n  Blue" + "Heron  \nsecond" + " word\n", "utf-8", newline="\n")
    patterns = Patterns.of_machine(words_file=words)
    assert patterns.words == ("Blue" + "Heron", "second" + " word")
    # the home and the host are read once, by `read_values`, and found as its kinds of a file
    assert patterns.values.home == (str(home),)
    assert patterns.values.host == (HOST_LONG, HOST)
    assert "home" in kinds(f"{home}{os.sep}x", patterns) and kinds(f"on {HOST.lower()}", patterns) == ["host"]
    assert kinds("a blueheron flew", patterns) == ["private_word"]


def test_a_missing_words_file_is_no_words(tmp_path):
    assert Patterns.of_machine(words_file=tmp_path / "none.txt").words == ()


def test_an_unreadable_words_file_is_a_coded_error_without_its_words(tmp_path):
    folder = tmp_path / "words.txt"
    folder.mkdir()
    with pytest.raises(ScanError) as caught:
        Patterns.of_machine(words_file=folder)
    assert caught.value.code == "words_unreadable"
    undecodable = tmp_path / "bad.txt"
    undecodable.write_bytes(b"fine\n\xff\xfe\xfa\n")
    with pytest.raises(ScanError) as caught:
        Patterns.of_machine(words_file=undecodable)
    assert caught.value.code == "words_unreadable"
    assert "fine" not in str(caught.value)


def test_the_words_file_defaults_to_the_setting_then_the_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    default = settings.data_folder() / "private_words.txt"
    default.parent.mkdir(parents=True)
    default.write_text("from" + "default\n", "utf-8", newline="\n")
    assert Patterns.of_machine().words == ("from" + "default",)
    named = tmp_path / "named.txt"
    named.write_text("from" + "setting\n", "utf-8", newline="\n")
    settings.save("private_words_file", str(named))
    assert Patterns.of_machine().words == ("from" + "setting",)
    settings.save("private_words_file", 7)
    with pytest.raises(settings.SettingsError):
        Patterns.of_machine()


def test_the_bare_user_name_is_not_a_pattern(tmp_path, monkeypatch):
    home = tmp_path / "somebody"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(socket, "gethostname", lambda: "localhost")
    patterns = Patterns.of_machine(words_file=tmp_path / "none.txt")
    assert kinds("hello somebody, welcome", patterns) == []


# --- a line of a million characters ---------------------------------------------------------

@pytest.mark.parametrize("filler", ["a", "sk-", "gh", "a@", ".a", "/home/", "-----BEGIN ", "AKIA"])
def test_a_line_of_a_million_characters_is_scanned_in_bounded_time(filler):
    line = (filler * (1_000_000 // len(filler) + 1))[:1_000_000]
    started = time.monotonic()
    Patterns().find(line)
    assert time.monotonic() - started < 20


# --- a repository of the fixture -------------------------------------------------------------

@pytest.fixture(scope="session")
def repo_template(tmp_path_factory):
    """Built once per worker; every test copies it (never writes into it)."""
    return make_template(tmp_path_factory.mktemp("template") / "repo")[0]


@pytest.fixture
def repo(tmp_path, repo_template):
    return Repo(tmp_path / "repo", template=repo_template)


def scan(repo, patterns=None, rev_range=None) -> list[Hit]:
    commits = scanner.commits_of_range(repo.path, rev_range or "HEAD")
    return scanner.scan_commits(repo.path, commits, patterns or Patterns())


def everywhere(hits, value) -> bool:
    """Whether `value` is in any text a hit makes."""

    return any(value in text for hit in hits for text in (str(hit), repr(hit), hit.file or ""))


# --- the added lines of a commit ------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(LINES))
def test_each_kind_is_found_in_the_lines_a_commit_adds_with_its_file_and_line(repo, kind):
    line, value = LINES[kind]
    repo.commit({"src/a.txt": "clean\n"})
    sha = repo.commit({"src/a.txt": f"clean\nstill clean\n{line}\n"})
    hits = scan(repo, rev_range="HEAD~1..HEAD")
    assert hits == [Hit(kind, "content", sha, "src/a.txt", 3)]
    assert not everywhere(hits, value)


def test_a_line_is_found_in_a_new_file_at_its_number_and_each_kind_is_a_hit_of_its_own(repo):
    sha = repo.commit({"new.txt": f"one\n{GITHUB_TOKEN} {SOMEONE_HOME}/x\nthree\n{AWS_KEY}\n"})
    assert scan(repo) == [Hit("home_path", "content", sha, "new.txt", 2),
                          Hit("github_token", "content", sha, "new.txt", 2),
                          Hit("aws_key", "content", sha, "new.txt", 4)]


def test_only_the_lines_a_commit_adds_count_not_the_ones_it_keeps_or_removes(repo):
    first = repo.commit({"a.txt": f"{GITHUB_TOKEN}\nkeep\n"})
    repo.commit({"a.txt": "keep\nnew\n"})
    assert scan(repo, rev_range="HEAD~1..HEAD") == []
    assert [h.commit for h in scan(repo)] == [first]


def test_lines_that_look_like_diff_headers_are_still_content(repo):
    sha = repo.commit({"a.txt": f"+++ b/x\n--- a/y\n@@ -1 +1 @@\ndiff --git a/q b/q\n{GITHUB_TOKEN}\n"})
    assert scan(repo) == [Hit("github_token", "content", sha, "a.txt", 5)]


def test_the_line_number_follows_the_hunks_of_a_changed_file(repo):
    repo.commit({"a.txt": "".join(f"line {n}\n" for n in range(1, 11))})
    lines = [f"line {n}\n" for n in range(1, 11)]
    lines[2:4] = ["changed\n"]
    lines.insert(7, f"{ANTHROPIC_KEY}\n")
    sha = repo.commit({"a.txt": "".join(lines)})
    assert scan(repo, rev_range="HEAD~1..HEAD") == [Hit("anthropic_key", "content", sha, "a.txt", 8)]


@pytest.mark.parametrize("home", [
    WIN_HOME + "\\x", WIN_HOME.replace("\\", "/") + "/x", MSYS_HOME + "/x",
    "/mnt/c/" + WIN_TAIL + "/x", "/cygdrive/c/" + WIN_TAIL + "/x",
    SOMEONE_HOME + "/x", MAC_HOME + "/x", "/ro" + "ot/x", WIN_HOME.replace("\\", "\\\\")])
def test_every_home_spelling_is_found_in_a_commit(repo, home):
    sha = repo.commit({"a.txt": f'path = "{home}"\n'})
    assert scan(repo) == [Hit("home_path", "content", sha, "a.txt", 1)]


def test_the_machine_home_and_host_and_words_are_found_in_a_commit(repo):
    patterns = with_values(home="D:" + "\\me", host=HOST, words=("Blue" + "Heron",))
    sha = repo.commit({"a.txt": "D:" + f"\\me\\x\nrunning on {HOST.lower()}\nthe blueheron\n"})
    assert scan(repo, patterns) == [Hit("home", "content", sha, "a.txt", 1),
                                    Hit("host", "content", sha, "a.txt", 2),
                                    Hit("private_word", "content", sha, "a.txt", 3)]
    assert scan(repo) == []


def test_the_look_alikes_are_no_hit_in_a_commit(repo):
    repo.commit({"a.txt": "\n".join([
        "C:" + "\\Us" + "ers\\<name>\\x", "/ho" + "me/$USER", "src/root.py", "rooted", "task-" + "a" * 30,
        "sk-short", "pytest.mark@x", "uses: actions/checkout@v4", "git@github.com:o/r.git",
        "Co-Authored-By: Claude <noreply@anthropic.com>", "1234+someone@users.noreply.github.com",
        "a@example.com"]) + "\n"})
    assert scan(repo) == []


# --- paths, message, author and committer ---------------------------------------------------

@pytest.mark.parametrize("kind", sorted(LINES))
def test_each_kind_is_found_in_the_path_of_a_file_a_commit_adds(repo, kind):
    value = LINES[kind][1]
    name = f"docs={value}/f.txt" if kind == "home_path" else f"docs/x {value}.dat"
    sha = repo.commit({name: "clean\n"})
    hits = scan(repo)
    assert hits == [Hit(kind, "path", sha, hits[0].file, None)]
    assert hits[0].file.startswith("docs") and f"<{kind}>" in hits[0].file
    assert not everywhere(hits, value)


def test_a_home_folder_in_a_path_is_found_where_a_name_ends_before_it(repo):
    sha = repo.commit({f"dir={SOMEONE_HOME}/f.txt": "clean\n", f"app{SOMEONE_HOME}/g.txt": "clean\n"})
    assert scan(repo) == [Hit("home_path", "path", sha, "dir=<home_path>/f.txt", None)]


def test_the_path_of_a_changed_file_is_scanned_and_that_of_a_removed_one_is_not(repo):
    first = repo.commit({f"key {GITHUB_TOKEN}.txt": "a\n", "other.txt": "a\n"}, message="seed")
    repo.commit({"other.txt": "b\n"}, remove=[f"key {GITHUB_TOKEN}.txt"])
    assert [(h.commit, h.where) for h in scan(repo)] == [(first, "path")]
    repo.commit({f"key {GITHUB_TOKEN}.txt": "c\n"}, message="back")
    repo.commit({f"key {GITHUB_TOKEN}.txt": "d\n"}, message="changed")
    assert [h.where for h in scan(repo, rev_range="HEAD~1..HEAD")] == ["path"]


def test_a_content_hit_in_a_file_with_a_value_in_its_name_does_not_print_the_name(repo):
    sha = repo.commit({f"key {GITHUB_TOKEN}.txt": f"{AWS_KEY}\n"})
    hits = scan(repo)
    assert {(h.kind, h.where) for h in hits} == {("github_token", "path"), ("aws_key", "content")}
    assert {h.file for h in hits} == {"key <github_token>.txt"}
    assert not everywhere(hits, GITHUB_TOKEN)
    assert {h.commit for h in hits} == {sha}


@pytest.mark.parametrize("kind", sorted(LINES))
def test_each_kind_is_found_in_a_commit_message_at_its_line(repo, kind):
    line, value = LINES[kind]
    sha = repo.commit({"a.txt": "clean\n"}, message=f"subject\n\nbody\n{line}\nlast")
    hits = scan(repo)
    assert hits == [Hit(kind, "message", sha, None, 4)]
    assert not everywhere(hits, value)


def test_a_noreply_trailer_in_a_message_is_no_hit(repo):
    repo.commit({"a.txt": "x\n"}, message="subject\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    assert scan(repo) == []


def test_the_author_and_the_committer_are_scanned_separately(repo):
    sha = repo.commit({"a.txt": "x\n"}, author=EMAIL)
    hits = scan(repo)
    assert hits == [Hit("email", "author", sha, None, None)]
    assert not everywhere(hits, EMAIL)
    sha = repo.commit({"a.txt": "y\n"}, committer=EMAIL)
    assert scan(repo, rev_range="HEAD~1..HEAD") == [Hit("email", "committer", sha, None, None)]
    sha = repo.commit({"a.txt": "z\n"}, author=EMAIL, committer=EMAIL)
    assert [(h.where, h.commit) for h in scan(repo, rev_range="HEAD~1..HEAD")] == [
        ("author", sha), ("committer", sha)]


def test_an_allowed_author_address_is_no_hit_and_another_is(repo):
    repo.commit({"a.txt": "x\n"}, author=EMAIL)
    allowed = Patterns(allowed_emails=[EMAIL])
    assert scan(repo, allowed) == []
    sha = repo.commit({"a.txt": "y\n"}, author="other" + "@" + "corp.io")
    assert scan(repo, allowed, "HEAD~1..HEAD") == [Hit("email", "author", sha, None, None)]


def test_the_machine_host_in_an_author_name_is_found(repo):
    repo.env |= {"GIT_AUTHOR_NAME": f"{HOST} admin"}
    sha = repo.commit({"a.txt": "x\n"})
    assert scan(repo, with_values(host=HOST.lower())) == [Hit("host", "author", sha, None, None)]


# --- the edges ------------------------------------------------------------------------------

def test_an_empty_range_has_no_commit_and_no_hit(repo):
    repo.commit({"a.txt": f"{GITHUB_TOKEN}\n"})
    assert scanner.commits_of_range(repo.path, "HEAD..HEAD") == []
    assert scanner.scan_commits(repo.path, [], Patterns()) == []


def test_a_range_lists_its_commits_oldest_first(repo):
    first = repo.commit({"a.txt": "1\n"})
    second = repo.commit({"a.txt": "2\n"})
    third = repo.commit({"a.txt": "3\n"})
    assert scanner.commits_of_range(repo.path, "HEAD") == [first, second, third]
    assert scanner.commits_of_range(repo.path, f"{first}..{third}") == [second, third]


def test_an_unknown_range_is_a_coded_error(repo):
    repo.commit({"a.txt": "1\n"})
    with pytest.raises(ScanError) as caught:
        scanner.commits_of_range(repo.path, "nope..alsonope")
    assert caught.value.code == "bad_rev"
    with pytest.raises(ScanError) as caught:
        scanner.commits_of_range(repo.path, "--all")
    assert caught.value.code == "bad_rev"


def test_a_folder_that_is_no_repository_is_a_coded_error(tmp_path):
    with pytest.raises(ScanError) as caught:
        scanner.commits_of_range(tmp_path, "HEAD")
    assert caught.value.code == "git_failed"


def test_a_commit_with_no_change_has_no_hit(repo):
    repo.commit({"a.txt": "x\n"})
    repo.commit(message="empty")
    assert scan(repo, rev_range="HEAD~1..HEAD") == []


def test_a_binary_file_gives_its_path_only(repo):
    sha = repo.commit({f"bin {GITHUB_TOKEN}.dat": b"\x00\x01\x02" + GITHUB_TOKEN.encode() + b"\x00",
                       "plain.bin": b"\x00" + SOMEONE_HOME.encode() + b"\x00"})
    assert [(h.kind, h.where, h.commit) for h in scan(repo)] == [("github_token", "path", sha)]


def test_a_file_that_is_not_utf8_is_read_with_replacement(repo):
    sha = repo.commit({"latin.txt": b"caf\xe9 \xff\n" + GITHUB_TOKEN.encode() + b" \xfe\n"})
    assert scan(repo) == [Hit("github_token", "content", sha, "latin.txt", 2)]


def test_a_file_with_a_line_of_a_million_characters_is_scanned(repo):
    big = "x" * 1_000_000
    sha = repo.commit({"big.txt": f"{big}\n{big} {GITHUB_TOKEN}\n{big}\n"})
    started = time.monotonic()
    assert scan(repo) == [Hit("github_token", "content", sha, "big.txt", 2)]
    assert time.monotonic() - started < 30


def test_a_file_name_with_spaces_and_non_ascii_letters_is_given_as_it_is(repo):
    name = "dir with space/файл é.txt"
    sha = repo.commit({name: f"{GITHUB_TOKEN}\n"})
    assert scan(repo) == [Hit("github_token", "content", sha, name, 1)]


def test_a_quoted_path_in_a_diff_header_is_read_back():
    assert scanner._path_of_header('diff --git "a/tab\\there" "b/tab\\there"') == "tab\there"
    assert scanner._path_of_header('diff --cc "q\\"uote\\\\"') == 'q"uote\\'
    assert scanner._path_of_header('diff --git "a/\\303\\251" "b/\\303\\251"') == "é"
    assert scanner._path_of_header("diff --git a/a b/x b/a b/x") == "a b/x"
    assert scanner._path_of_header("diff --cc dir/f g.txt") == "dir/f g.txt"


def two_branches(repo):
    repo.commit({"base.txt": "base\n", "f.txt": "one\n"})
    repo.git("checkout", "-q", "-b", "side")
    side = repo.commit({"side.txt": f"{GITHUB_TOKEN}\n", "f.txt": "one\ntwo\n"})
    repo.git("checkout", "-q", "main")
    main = repo.commit({"main.txt": "clean\n", "g.txt": f"{AWS_KEY}\n"})
    return side, main


def test_a_merge_commit_counts_only_the_lines_new_against_every_parent(repo):
    side, main = two_branches(repo)
    repo.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
    merge = repo.head
    assert {h.commit for h in scan(repo)} == {side, main}
    assert scan(repo, rev_range=f"{main}..{merge}") == [Hit("github_token", "content", side, "side.txt", 1)]
    assert scanner.commits_of_range(repo.path, f"{main}..{merge}")[-1] == merge


def test_the_lines_a_merge_resolves_itself_count_for_the_merge(repo):
    repo.commit({"f.txt": "base\n"})
    repo.git("checkout", "-q", "-b", "side")
    repo.commit({"f.txt": "side\n"})
    repo.git("checkout", "-q", "main")
    repo.commit({"f.txt": "main\n"})
    repo.git("merge", "side", check=False)
    (repo.path / "f.txt").write_text(f"resolved\n{GITHUB_TOKEN}\n", "utf-8", newline="\n")
    repo.git("add", "f.txt")
    repo.git("commit", "-q", "-m", "resolve")
    merge = repo.head
    assert scan(repo, rev_range=f"{merge}~1..{merge}") == [Hit("github_token", "content", merge, "f.txt", 2)]


# --- the command ----------------------------------------------------------------------------

def guard(capsys, repo, *argv):
    code = main(["guards", "scan", "--work-tree", str(repo.path), *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_clean_range_prints_the_totals_and_exits_0(repo, capsys):
    repo.commit({"a.txt": "fine\n"})
    repo.commit({"a.txt": "still fine\n"})
    assert guard(capsys, repo, "--range", "HEAD~1..HEAD") == (0, "hits=0 commits=1\n", "")
    assert guard(capsys, repo, "--all", "--machine-independent") == (0, "hits=0 commits=2\n", "")


@pytest.mark.parametrize("kind", sorted(LINES))
def test_each_hit_is_one_line_of_sha_where_file_line_and_kind_and_the_exit_is_1(repo, capsys, kind):
    line, value = LINES[kind]
    repo.commit({"src/a.txt": "clean\n"})
    sha = repo.commit({"src/a.txt": f"clean\n{line}\n"}, message=f"subject\n\n{line}")
    code, out, err = guard(capsys, repo, "--range", "HEAD~1..HEAD", "--machine-independent")
    assert (code, err) == (1, "")
    assert out == f"{sha[:12]} message -:3 {kind}\n{sha[:12]} content src/a.txt:2 {kind}\nhits=2 commits=1\n"
    assert value not in out


def test_a_path_a_name_and_a_time_in_the_author_print_with_dashes_where_a_part_is_missing(repo, capsys):
    sha = repo.commit({"docs/x " + GITHUB_TOKEN + ".txt": "clean\n"}, author=EMAIL)
    code, out, _ = guard(capsys, repo, "--all", "--machine-independent")
    assert code == 1
    assert out == (f"{sha[:12]} author -:- email\n{sha[:12]} path docs/x <github_token>.txt:- github_token\n"
                   "hits=2 commits=1\n")
    assert GITHUB_TOKEN not in out and EMAIL not in out


def test_the_tree_option_scans_the_files_at_a_revision(repo, capsys):
    first = repo.commit({"a.txt": f"{GITHUB_TOKEN}\n"})
    repo.commit(remove=["a.txt"])
    assert guard(capsys, repo, "--tree", "--machine-independent") == (0, "hits=0 commits=1\n", "")
    code, out, _ = guard(capsys, repo, "--tree", "--rev", "HEAD~1", "--machine-independent")
    assert (code, out) == (1, f"{first[:12]} content a.txt:1 github_token\nhits=1 commits=1\n")


def test_one_of_range_tree_or_all_is_required_and_a_rev_needs_the_tree(repo, capsys):
    assert guard(capsys, repo)[0] == 2
    assert guard(capsys, repo, "--range", "HEAD", "--tree")[0] == 2
    repo.commit({"a.txt": "x\n"})
    code, out, err = guard(capsys, repo, "--range", "HEAD", "--rev", "HEAD")
    assert (code, out, err.split(":")[0]) == (2, "", "error")
    assert "bad_options" in err


def test_a_coded_error_prints_error_and_exits_2_without_any_value(repo, capsys):
    repo.commit({"a.txt": f"{GITHUB_TOKEN}\n"})
    code, out, err = guard(capsys, repo, "--range", "nope..alsonope")
    assert (code, out) == (2, "")
    assert err.startswith("error: bad_rev")
    code, out, err = guard(capsys, repo, "--all", "--words-file", str(repo.path))
    assert (code, out) == (2, "")
    assert err.startswith("error: words_unreadable")
    assert GITHUB_TOKEN not in err


def test_the_machine_own_patterns_are_on_unless_machine_independent_is_given(repo, capsys, tmp_path):
    words = tmp_path / "words.txt"
    words.write_text("Blue" + "Heron\n", "utf-8", newline="\n")
    sha = repo.commit({"a.txt": "a blueheron flew\n"})
    code, out, _ = guard(capsys, repo, "--all", "--words-file", str(words))
    assert (code, out) == (1, f"{sha[:12]} content a.txt:1 private_word\nhits=1 commits=1\n")
    assert guard(capsys, repo, "--all", "--machine-independent")[:2] == (0, "hits=0 commits=1\n")
    assert guard(capsys, repo, "--all", "--machine-independent", "--words-file", str(words))[0] == 2


def test_allowed_addresses_are_no_hit(repo, capsys):
    repo.commit({"a.txt": f"by {EMAIL}\n"}, author="other" + "@" + "corp.io")
    code, out, _ = guard(capsys, repo, "--all", "--machine-independent")
    assert code == 1 and out.endswith("hits=2 commits=1\n")
    allowed = guard(capsys, repo, "--all", "--machine-independent", "--allow-email", EMAIL.upper(),
                    "--allow-email", "other" + "@" + "corp.io")
    assert allowed == (0, "hits=0 commits=1\n", "")


def test_the_public_email_of_a_github_login_is_allowed_and_an_unreadable_one_is_warned_about(
        repo, capsys, monkeypatch):
    emails = {"users/jane": EMAIL, "users/ghost": "null"}
    asked = []

    def gh(args):
        asked.append(args)
        if args[1] not in emails:
            raise github.GithubError("gh_failed", "boom")
        return emails[args[1]] + "\n"

    monkeypatch.setattr(github, "run_gh", gh)
    repo.commit({"a.txt": f"by {EMAIL}\n"})
    code, out, err = guard(capsys, repo, "--all", "--machine-independent", "--allow-email-of", "jane")
    assert (code, out, err) == (0, "hits=0 commits=1\n", "")
    assert asked == [["api", "users/jane", "--jq", ".email"]]
    code, out, err = guard(capsys, repo, "--all", "--machine-independent", "--allow-email-of", "ghost",
                           "--allow-email-of", "broken", "--allow-email-of", "bad/login")
    assert code == 1 and out.endswith("hits=1 commits=1\n")
    assert err.splitlines() == ["warning: email_of_unreadable: ghost", "warning: email_of_unreadable: broken",
                                "warning: email_of_unreadable: bad/login"]
    assert EMAIL not in err and asked[-1] == ["api", "users/broken", "--jq", ".email"]


def test_a_file_name_that_cannot_be_printed_in_the_console_encoding_still_prints(repo, capsys, monkeypatch):
    name = "файл.txt"
    repo.commit({name: f"{GITHUB_TOKEN}\n"})
    class Narrow:
        encoding = "ascii"

        def __init__(self):
            self.written = ""

        def write(self, text):
            self.written += text.encode("ascii").decode("ascii")

        def flush(self):
            pass

    narrow = Narrow()
    monkeypatch.setattr(sys, "stdout", narrow)
    assert main(["guards", "scan", "--work-tree", str(repo.path), "--all", "--machine-independent"]) == 1
    assert "\\u0444\\u0430\\u0439\\u043b.txt:1 github_token" in narrow.written


# --- the real case: the JUnit fixtures of part 2 (#395 row 8) -------------------------------

ROOT = Path(__file__).parents[1]
PRE_SCRUB = "d68862b"
JUNIT = "tests/fixtures/junit"


def real_git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, timeout=60,
                          creationflags=NO_WINDOW)


def test_the_junit_fixtures_at_head_hold_no_home_path():
    """The reports are what tools wrote on a machine, so a home folder can get into them. (Other
    fixtures are prose or invented values, and may name a folder like `/root` on purpose.)"""

    if real_git("rev-parse", "--verify", "HEAD").returncode != 0:
        pytest.skip("no git history here")
    hits = scanner.scan_tree(ROOT, Patterns())
    assert [(h.kind, h.file, h.line) for h in hits
            if h.kind == "home_path" and (h.file or "").startswith(f"{JUNIT}/")] == []


@pytest.mark.parametrize("tool", ["go", "pytest"])
@pytest.mark.parametrize("scenario", ["empty", "green", "red"])
def test_the_host_name_of_a_fixture_report_is_the_neutral_one(tool, scenario):
    text = (ROOT / JUNIT / tool / f"{scenario}.xml").read_text("utf-8")
    assert set(re.findall(r'hostname="([^"]*)"', text)) == {"fixture-host"}


def test_the_reports_as_part_2_committed_them_held_a_home_path_on_17_lines():
    """The scanner finds what really stood in this repository's `main` at `d68862b`: the home
    folder of the machine that wrote the reports, in 15 stack frames of the jest report and one
    skip location in each of the pytest `red` and `green` reports (the ticket counted 16: it
    missed the `green` one)."""

    if real_git("cat-file", "-e", f"{PRE_SCRUB}^{{commit}}").returncode != 0:
        pytest.skip(f"{PRE_SCRUB} is not in this clone (a shallow one)")
    names = real_git("ls-tree", "-r", "--name-only", PRE_SCRUB, "tests/fixtures/").stdout.decode().split()
    patterns = Patterns()
    found: dict[str, list[int]] = {}
    for name in names:
        text = real_git("show", f"{PRE_SCRUB}:{name}").stdout.decode("utf-8", "replace")
        lines = [n for n, line in enumerate(text.split("\n"), 1) if "home_path" in patterns.find(line)]
        if lines:
            found[name] = lines
    assert found == {f"{JUNIT}/jest/red.xml": [11, 12, *range(14, 16), *range(17, 28)],
                     f"{JUNIT}/pytest/red.xml": [5], f"{JUNIT}/pytest/green.xml": [1]}


# --- git that hangs, and a git environment that is not ours ------------------------------------

def a_git_that_says(monkeypatch, script, seconds=1):
    """Stand in for the git program with a Python one-liner, and bound a wait to `seconds`."""

    monkeypatch.setattr(scanner, "_GIT_COMMAND", (sys.executable, "-c", script))
    monkeypatch.setattr(scanner, "GIT_TIMEOUT_SECONDS", seconds)


def test_a_git_that_hangs_is_killed_within_the_bound_and_is_a_git_timeout(repo, monkeypatch):
    repo.commit({"a.txt": "x\n"})
    a_git_that_says(monkeypatch, "import time; time.sleep(120)")
    started = time.monotonic()
    for call in (lambda: scanner.commits_of_range(repo.path, "HEAD"),
                 lambda: scanner.scan_commits(repo.path, ["0" * 40], Patterns()),
                 lambda: scanner.scan_tree(repo.path, Patterns())):
        with pytest.raises(ScanError) as caught:
            call()
        assert caught.value.code == "git_timeout"
    assert time.monotonic() - started < 40


def test_a_git_that_stops_midway_in_a_stream_is_a_git_timeout(repo, monkeypatch):
    header = "'\\x00COMMIT\\x00' + '\\x00'.join(['a' * 40, 'n', 'e@users.noreply.github.com', 'n', 'e', 'subject'])"
    a_git_that_says(monkeypatch, f"import time; print({header}, flush=True); time.sleep(120)")
    started = time.monotonic()
    with pytest.raises(ScanError) as caught:
        scanner.scan_commits(repo.path, ["a" * 40], Patterns())
    assert caught.value.code == "git_timeout"
    assert time.monotonic() - started < 20


def test_a_git_that_keeps_talking_is_not_cut_off_by_a_bound_on_silence(repo, monkeypatch):
    a_git_that_says(monkeypatch, "import time\nfor n in range(8):\n    print('%040d' % n, flush=True)\n"
                                 "    time.sleep(0.4)")
    assert len(scanner.commits_of_range(repo.path, "HEAD")) == 8


def test_git_runs_in_an_environment_without_a_repository_of_someone_elses_and_in_the_c_locale(monkeypatch):
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        monkeypatch.setenv(name, "elsewhere")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("KEEP_ME", "1")
    env = scanner._git_env()
    assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"} & set(env)
    assert env["LC_ALL"] == "C" and env["KEEP_ME"] == "1"


def test_a_git_dir_in_the_environment_does_not_move_the_scan_to_another_repository(
        repo, tmp_path, monkeypatch, capsys):
    sha = repo.commit({"a.txt": f"{GITHUB_TOKEN}\n"})
    other = Repo(tmp_path / "other")
    other.commit({"b.txt": "clean\n"})
    monkeypatch.setenv("GIT_DIR", str(other.path / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other.path))
    assert scan(repo) == [Hit("github_token", "content", sha, "a.txt", 1)]
    assert scanner.scan_tree(repo.path, Patterns()) == [
        Hit("github_token", "content", sha, "a.txt", 1)]
    code, out, _ = guard(capsys, repo, "--all", "--machine-independent")
    assert (code, out) == (1, f"{sha[:12]} content a.txt:1 github_token\nhits=1 commits=1\n")


# --- the files of a tree --------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(LINES))
def test_scan_tree_finds_each_kind_in_a_tracked_file_and_its_path(repo, kind):
    line, value = LINES[kind]
    name = f"docs={value}/f.txt" if kind == "home_path" else f"docs/x {value}.dat"
    sha = repo.commit({name: f"clean\n{line}\n", "ok.txt": "fine\n"})
    hits = scanner.scan_tree(repo.path, Patterns())
    shown = hits[0].file
    assert hits == [Hit(kind, "path", sha, shown, None), Hit(kind, "content", sha, shown, 2)]
    assert not everywhere(hits, value)


def test_scan_tree_reads_the_files_at_the_revision_it_is_given(repo):
    first = repo.commit({"a.txt": f"x\n{GITHUB_TOKEN}\n"})
    repo.commit(remove=["a.txt"])
    (repo.path / "untracked.txt").write_text(f"{AWS_KEY}\n", "utf-8", newline="\n")
    assert scanner.scan_tree(repo.path, Patterns()) == []
    old = scanner.scan_tree(repo.path, Patterns(), "HEAD~1")
    assert old == [Hit("github_token", "content", first, "a.txt", 2)]


def test_scan_tree_gives_the_path_of_a_binary_file_only(repo):
    sha = repo.commit({f"bin {GITHUB_TOKEN}.dat": b"\x00" + GITHUB_TOKEN.encode(),
                       "café dir/latin.txt": b"caf\xe9\n" + AWS_KEY.encode() + b"\n"})
    hits = scanner.scan_tree(repo.path, Patterns())
    assert sorted((h.kind, h.where, h.file, h.line) for h in hits) == [
        ("aws_key", "content", "café dir/latin.txt", 2),
        ("github_token", "path", "bin <github_token>.dat", None)]
    assert {h.commit for h in hits} == {sha}


def test_scan_tree_reads_more_files_than_one_batch(repo):
    files = {f"d{n % 7}/f{n}.txt": f"line {n}\n" for n in range(250)}
    files["d4/f249.txt"] += f"{OPENAI_KEY}\n"
    sha = repo.commit(files)
    assert scanner.scan_tree(repo.path, Patterns()) == [
        Hit("openai_key", "content", sha, "d4/f249.txt", 2)]


def test_scan_tree_of_an_unknown_revision_is_a_coded_error(repo):
    repo.commit({"a.txt": "x\n"})
    with pytest.raises(ScanError) as caught:
        scanner.scan_tree(repo.path, Patterns(), "nope")
    assert caught.value.code == "bad_rev"


def test_a_line_a_merge_takes_from_one_parent_is_not_new_in_the_merge(repo):
    repo.commit({"f.txt": "base\n"})
    repo.git("checkout", "-q", "-b", "side")
    side = repo.commit({"f.txt": f"{GITHUB_TOKEN}\n"})
    repo.git("checkout", "-q", "main")
    repo.commit({"f.txt": "main\n"})
    repo.git("merge", "side", check=False)
    (repo.path / "f.txt").write_text(f"main\n{GITHUB_TOKEN}\n{AWS_KEY}\n", "utf-8", newline="\n")
    repo.git("add", "f.txt")
    repo.git("commit", "-q", "-m", "resolve")
    merge = repo.head
    hits = scan(repo, rev_range=f"{merge}~1..{merge}")
    assert hits == [Hit("github_token", "content", side, "f.txt", 1),
                    Hit("aws_key", "content", merge, "f.txt", 3)]


def test_a_range_with_two_hundred_commits_is_scanned_in_bounded_time(repo):
    stream = []
    for n in range(200):
        message = f"commit {n}"
        content = f"line {n}\n" + (f"{GITHUB_TOKEN}\n" if n % 50 == 7 else "")
        stream.append(
            f"commit refs/heads/main\ncommitter Fixture <{NOREPLY}> {1_700_000_000 + n} +0000\n"
            f"data {len(message)}\n{message}\nM 100644 inline f.txt\ndata {len(content)}\n{content}\n")
    repo.git("fast-import", "--quiet", input="".join(stream).encode("utf-8"))
    repo.git("reset", "-q", "--hard", "main")
    started = time.monotonic()
    hits = scan(repo)
    assert time.monotonic() - started < 60
    assert [(h.kind, h.line) for h in hits] == [("github_token", 2)] * 4
    assert len(scanner.commits_of_range(repo.path, "HEAD")) == 200


# #415 unit 7: the push scan judges a file with the same allowed references as the text hook
def test_an_allowed_reference_is_no_push_finding_and_a_bare_account_beside_it_is():
    account = "acme" + "-dev"  # the account of the invented setup (scan_setup.INVENTED)
    link = f"https://github.com/{account}/orch-tools/issues/415"
    patterns = with_values()
    assert patterns.find_in_file("src/notes.py", f"Refs {link}\n") == []
    assert patterns.find_in_file("src/notes.py", f"Refs {link}\nthe owner {account} wrote it\n") == [("account", 2)]
