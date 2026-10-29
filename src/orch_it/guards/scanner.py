"""The private-data scanner of #395 (decision 4): one seam for the pre-push hook, the linux job's
step and the history run.

It looks for nine kinds of private data in the lines a commit adds, in the paths it adds or
changes, in its message, and in its author and committer. A hit says the kind and where it is;
it never carries or prints the value found. Nothing here writes events.

`Patterns.find` is the whole rulebook for one line of text; `scan_commits` and `scan_tree` feed
it lines out of `git`, streaming, so a commit of any size is read once.

The same scan carries the coding standard of #413 (CODING_STANDARDS.md): a value that varies is
never a literal. `Patterns.of_machine` and `Patterns.of_run` read the setup's values ONCE
(`values.read_values`: account, repo, host, home, user, projects folder, git name and e-mail); those
and the pattern kinds of `kinds.py` (bare python, a one-OS call, a check name) go through
`Patterns.find_in_file`, beside the line rules above. It judges the text of a whole file (a
pattern kind needs the lines around the one added) and leaves out the allowed places and the
exceptions list. A hit of those kinds is cited with the table's `coding_rule` (`cited`), and
`Patterns.not_checked_line` names the kinds the setup lacked.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from orch_it import language, settings
from orch_it.guards import exceptions as exceptions_list
from orch_it.guards import kinds as pattern_kinds
from orch_it.guards import values as setup_values

KINDS = ("home_path", "host_name", "github_token", "anthropic_key", "openai_key", "aws_key",
         "private_key", "email", "private_word")
# the kinds of `find_in_file`, beside the nine of `KINDS`
FILE_KINDS = (*setup_values.KINDS, *pattern_kinds.KINDS)
# the first sentence of CODING_STANDARDS.md, word for word, in English: the reference the tests hold the en table's
# `coding_rule` and the standard to; no printer reads it, `cited` and `coding_rule` read the sentence from the setting's table
RULE = ("A value that differs per repo, account, machine or OS is a setting with its default, never a literal"
        " in code, tests, rules or tickets.")
# the setup's own values `find` matches in every line, path, message and author
_OWN_KINDS = ("home", "host")
# the kinds the rule judges: the setup's own values and the pattern kinds; the kinds of #395 keep their lines
RULE_KINDS = frozenset(FILE_KINDS)
WHERES = ("content", "path", "message", "author", "committer")
SCAN_ERROR_CODES = ("words_unreadable", "git_failed", "bad_rev", "git_timeout")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# a git that says nothing for this long is hung: it is killed and the scan fails
GIT_TIMEOUT_SECONDS = 120

_NAME = r"[A-Za-z0-9_][A-Za-z0-9._-]*"
_SEP = r"(?:\\\\|\\|/)"
_BOUNDARY = r"(?<![A-Za-z0-9_-])"
# A POSIX home folder starts a path: nothing of a path or a name stands right before its slash
# (`app/home/x`, `host/Users/list`, `~/home/x`, `${HOME}/home/x` and `$(pwd)/home/x` are not home
# folders), unless it is the path of a `file://` URL.
_PATH_START = r"(?:(?<![A-Za-z0-9_/.\\~})-])|(?<=file://))"
# the folders every machine has, which belong to no one
_NOT_A_PERSON = r"(?!(?i:public|default user|default|shared|runneradmin|runner|all users)(?![A-Za-z0-9._-]))"

# GENERIC shapes, not the setup's values: any user's home folder in the spellings a path takes in logs and tracebacks. `<name>` is a
# real name: a placeholder (`<name>`, `$USER`, `%USERNAME%`) never starts with a name character.
_ANY_HOME = re.compile(
    rf"(?<![A-Za-z])[A-Za-z]:{_SEP}(?i:users){_SEP}{_NOT_A_PERSON}{_NAME}"
    rf"|{_PATH_START}/(?:(?:cygdrive|mnt)/)?[A-Za-z]/(?i:users)/{_NOT_A_PERSON}{_NAME}"
    rf"|{_PATH_START}/home/{_NOT_A_PERSON}{_NAME}"
    rf"|{_PATH_START}/Users/{_NOT_A_PERSON}{_NAME}"
    # a bare `/root` in a code span is prose about the folder, not a path in use
    rf"|{_PATH_START}(?:(?<!`)/root(?![A-Za-z0-9._-])|(?<=`)/root(?![A-Za-z0-9._`-]))")

# The secrets by their published shapes, each with a left boundary.
_SECRET = re.compile(
    rf"{_BOUNDARY}(?:"
    r"(?P<github_token>gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"
    r"|(?P<anthropic_key>sk-ant-[A-Za-z0-9_-]{16,})"
    r"|(?P<openai_key>sk-(?!ant-)[A-Za-z0-9_-]{20,})"
    r"|(?P<aws_key>AKIA[0-9A-Z]{16}(?![A-Za-z0-9_-]))"
    r"|(?P<private_key>-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----)"
    r")")

# GENERIC shapes, not the setup's values: the names an OS gives a machine nobody named (Windows setup, upper-case), which are a host name
# on any machine: found by their shape, so a job on a runner catches a leaked one (#395 decision 4,
# brain answer on the generic host-name shapes).
_DEFAULT_HOST = re.compile(
    r"(?<![A-Za-z0-9_-])(?:WIN-[A-Z0-9]{8,15}|DESKTOP-[A-Z0-9]{7}|LAPTOP-[A-Z0-9]{8})(?![A-Za-z0-9_-])")

_LOCAL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+-")
_DOMAIN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-")
_DOMAIN = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_DOMAIN_LONGEST = 255
# reserved by RFC 2606 and 6761: no person has an address there
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
_RESERVED_ENDINGS = ("example", "test", "invalid", "localhost")
_NO_PERSON_LOCALS = ("noreply", "no-reply", "git")
# `logo@2x.png` is an asset name, not an address
_FILE_ENDINGS = ("png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "css", "js", "map", "json", "md",
                 "txt", "lock")


class ScanError(Exception):
    """A scan that cannot run. `code` is one of `SCAN_ERROR_CODES`: `words_unreadable` (the
    private words file exists and cannot be read), `git_failed` (a git command failed; the
    detail is git's own message), `bad_rev` (a range or revision git does not know),
    `git_timeout` (a git said nothing for `GIT_TIMEOUT_SECONDS` and was killed)."""

    def __init__(self, code: str, detail: str = ""):
        if code not in SCAN_ERROR_CODES:
            raise ValueError(f"unknown scan error code: {code!r}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class Hit:
    """One private value found. `where`: one of `WHERES` (`content`, `path`, `message`, `author`
    or `committer`); `commit`: the full sha; `file`: the path (content and path hits) with any value found in it
    replaced by `<kind>`, else None; `line`: the line in the new file (content) or in the
    message (message), else None. The value itself is in no field."""

    kind: str
    where: str
    commit: str
    file: str | None
    line: int | None


def _private_email(local: str, domain: str, allowed: frozenset[str]) -> bool:
    """Whether `local@domain` is an address of a person who is not allowed."""

    domain = domain.lower()
    if local.lower() in _NO_PERSON_LOCALS or domain == "users.noreply.github.com":
        return False
    if domain.rsplit(".", 1)[-1] in _FILE_ENDINGS:
        return False
    if any(domain == d or domain.endswith("." + d) for d in _RESERVED_DOMAINS):
        return False
    if any(domain == d or domain.endswith("." + d) for d in _RESERVED_ENDINGS):
        return False
    return f"{local}@{domain}".lower() not in allowed


@dataclass(frozen=True)
class Patterns:
    """What to look for. `words`: private words and phrases; `allowed_emails`: addresses that are
    no hit. The secrets, the generic shapes of ANY user's home folder and of an OS's default host
    name (foreign values, not the setup's) and the addresses of people are always on, unless
    `old_kinds` is False (a push to a private remote keeps only the kinds of #413).

    `values` (None: the kinds above only) is what `read_values` read of the setup: its home and
    host are matched by `find` (a line, a path, a message, an author), the other six kinds by
    `find_in_file`. `exceptions` is the list of `guards-exceptions.toml`, `job_names` the checks of
    the workflows and `settings_file` the repo path of the settings file; with `values` set,
    `find_in_file` judges the text of a file by the pattern kinds too. The setup's own values have
    one matcher, that of `values.py`; there is no second one here."""

    words: tuple[str, ...] = ()
    allowed_emails: frozenset[str] = frozenset()
    values: setup_values.Values | None = None
    exceptions: exceptions_list.Exceptions = exceptions_list.Exceptions()
    job_names: frozenset[str] = frozenset()
    settings_file: str = ""
    old_kinds: bool = True

    def __post_init__(self):
        object.__setattr__(self, "allowed_emails",
                           frozenset(address.lower() for address in self.allowed_emails))

    def without_old_kinds(self) -> Patterns:
        """These patterns with the kinds of #395 off: only the setup's values and the pattern kinds."""

        return dataclasses.replace(self, old_kinds=False)

    @classmethod
    def of_machine(cls, *, allowed_emails=(), words_file: Path | None = None, work_tree: Path | str | None = None,
                   rev: str | None = None, exceptions: exceptions_list.Exceptions | None = None,
                   env=None, run=None, home=None, host=None) -> Patterns:
        """The patterns of this machine, measured now: the values of its setup (`read_values`, once;
        the git of `work_tree`, default the current folder, which is where the CLI runs), the
        exceptions list, job names and settings file of `work_tree` (or of the tree of commit `rev`;
        `exceptions` replaces the list), and the private words file (default the setting
        `private_words_file`, else `private_words.txt` in the data folder; a missing file is no
        words). `env`, `run`, `home` and `host` go to `read_values` as they are. Raises
        `ExceptionsError` for an exceptions list with an entry that cannot be used."""

        return cls._of_setup(Path(work_tree or Path.cwd()), words=_read_words(words_file),
                             allowed_emails=allowed_emails, rev=rev, exceptions=exceptions, env=env, run=run,
                             home=home, host=host)

    @classmethod
    def of_run(cls, *, allowed_emails=(), work_tree: Path | str | None = None, rev: str | None = None,
               exceptions: exceptions_list.Exceptions | None = None, env=None, run=None, home=None,
               host=None) -> Patterns:
        """The patterns of the run this is in (a job on a runner): the same values as `of_machine`,
        read from the run's own environment (on GitHub the account, repo, runner home, user and
        host), and no file of the owner's: no private words."""

        return cls._of_setup(Path(work_tree or Path.cwd()), words=(), allowed_emails=allowed_emails, rev=rev,
                             exceptions=exceptions, env=env, run=run, home=home, host=host)

    @classmethod
    def _of_setup(cls, work_tree: Path, *, words, allowed_emails, rev, exceptions, env, run, home,
                  host) -> Patterns:
        load, job_names = _tree_files(work_tree, rev)
        found = setup_values.read_values(env=env, run=run or _git_in(work_tree), home=home, host=host)
        return cls(words=words, allowed_emails=frozenset(allowed_emails), values=found,
                   exceptions=load() if exceptions is None else exceptions, job_names=job_names,
                   settings_file=_repo_path(work_tree, settings.settings_path()))

    @functools.cached_property
    def _words(self) -> re.Pattern | None:
        alternatives = []
        for word in sorted(set(self.words), key=len, reverse=True):
            left = r"(?<!\w)" if re.match(r"\w", word) else ""
            right = r"(?!\w)" if re.search(r"\w$", word) else ""
            alternatives.append(left + re.escape(word) + right)
        return re.compile("|".join(alternatives), re.IGNORECASE) if alternatives else None

    @functools.cached_property
    def _own(self) -> tuple[tuple[str, re.Pattern], ...]:
        """The setup's home and host as (kind, regex), from the one matcher of `values.py`."""

        if self.values is None:
            return ()
        return tuple((kind, pattern) for kind in _OWN_KINDS for value in getattr(self.values, kind) or ()
                     if (pattern := setup_values._pattern(kind, value)) is not None)

    def _emails(self, text: str) -> Iterator[tuple[int, int]]:
        """The spans of the addresses of people in `text`. Found from each `@` outward, so a
        long line with no `@` costs one pass."""

        at = text.find("@")
        while at != -1:
            start = at
            while start > 0 and text[start - 1] in _LOCAL_CHARS:
                start -= 1
            stop = at + 1
            while stop < len(text) and stop - at <= _DOMAIN_LONGEST and text[stop] in _DOMAIN_CHARS:
                stop += 1
            domain = _DOMAIN.match(text, at + 1, stop) if start < at else None
            if domain and _private_email(text[start:at], domain.group(), self.allowed_emails):
                yield start, domain.end()
            at = text.find("@", at + 1)

    def _spans(self, text: str) -> list[tuple[str, int, int]]:
        """Every value found in `text` as (kind, start, end), sorted by position."""

        spans: list[tuple[str, int, int]] = []
        for kind, pattern in self._own:
            spans += [(kind, m.start(), m.end()) for m in pattern.finditer(text)]
        if self.old_kinds:
            for pattern, kind in ((_ANY_HOME, "home_path"), (_DEFAULT_HOST, "host_name"),
                                  (self._words, "private_word")):
                if pattern is not None:
                    spans += [(kind, m.start(), m.end()) for m in pattern.finditer(text)]
            spans += [(m.lastgroup, m.start(), m.end()) for m in _SECRET.finditer(text)]
            spans += [("email", start, end) for start, end in self._emails(text)]
        return sorted(spans, key=lambda span: (span[1], -span[2]))

    def find(self, text: str) -> list[str]:
        """The kinds found in one line of text, each once, in the order of `KINDS`, then the setup's
        own `home` and `host`."""

        found = {kind for kind, _, _ in self._spans(text)}
        return [kind for kind in (*KINDS, *_OWN_KINDS) if kind in found]

    def find_in_file(self, path: str, text: str) -> list[tuple[str, int]]:
        """(kind, 1-based line) of each value and pattern kind of #413 found in `text`, the whole
        text of the file at the repo path `path`, sorted by line. Empty when `values` is None, for a
        path that is an allowed place, and for a (line, kind) an exceptions entry allows. The setup's own
        home and host are left to `find`."""

        if self.values is None or exceptions_list.is_allowed_place(path, self.settings_file):
            return []
        found = {(hit.kind, hit.line) for hit in setup_values.find_in(text, self.values)
                 if hit.kind not in _OWN_KINDS}  # the home and host are `find`'s
        found |= {(hit.kind, hit.line) for hit in pattern_kinds.find_pattern_kinds(path, text, self.job_names)}
        lines = text.split("\n")
        return sorted((kind, number) for kind, number in found
                      if not self.exceptions.allows(path, lines[number - 1] if number <= len(lines) else "", kind))

    def not_checked_line(self) -> str:
        """`not checked: git_email (not set), ...` for the kinds of values the setup lacked, else
        empty. A result of a scan with this line non-empty carries it, clean or not."""

        return self.values.render_not_checked() if self.values is not None else ""

    def redact(self, text: str) -> str:
        """`text` with every value found in it replaced by `<kind>`; values that overlap or touch
        are one replaced stretch, so no remainder of a value is left."""

        merged: list[list] = []
        for kind, start, stop in self._spans(text):
            if merged and start <= merged[-1][2]:
                merged[-1][2] = max(merged[-1][2], stop)
            else:
                merged.append([kind, start, stop])
        out, end = [], 0
        for kind, start, stop in merged:
            out += [text[end:start], f"<{kind}>"]
            end = stop
        return "".join(out) + text[end:]


def coding_rule() -> str:
    """The rule a finding is cited with, in the setting's language (the table's `coding_rule`). It is read from the
    table on each call, so a printer of many hits takes it once and passes it to `cited`."""

    return language.text_or_refusal("coding_rule")


def cited(kind: str, rule: str | None = None) -> str:
    """The text a line naming a hit of `kind` ends with: the rule, for a kind it judges, else nothing. `rule` is
    what `coding_rule()` gave, when the printer took it already; left out, it is read here."""

    if kind not in RULE_KINDS:
        return ""
    return f" - {coding_rule() if rule is None else rule}"


def _git_in(work_tree: Path):
    """The `run` of `read_values` for a scan of `work_tree`: git asked there, as the scan's git is
    (no GIT_DIR of a hook), anything else as `read_values` runs it."""

    def run(cmd: list[str]) -> str:
        if cmd and cmd[0] == _GIT_COMMAND[0]:
            return _git_output(work_tree, *cmd[1:]).decode("utf-8", "replace")
        return setup_values.default_run(cmd)

    return run


def sha_of(work_tree: Path, rev: str = "HEAD") -> str:
    """The full sha of commit `rev`; raises `ScanError` (`bad_rev`) when git does not know it."""

    try:
        return _git_output(work_tree, "rev-parse", "--verify", "--end-of-options",
                           f"{rev}^{{commit}}").decode("ascii", "replace").strip()
    except ScanError as e:
        raise _unknown_rev(e, "the revision") from None


def _tree_files(work_tree: Path, rev: str | None):
    """(a call that loads the exceptions list, the job names) of the work tree, or of the tree of commit `rev`
    (its `guards-exceptions.toml` and the `*.yml` / `*.yaml` of `.github/workflows`, read as raw blobs by `git cat-file blob`, no filter applied)."""

    if rev is None:
        return (lambda: exceptions_list.load_exceptions(work_tree / exceptions_list.FILE_NAME),
                pattern_kinds.read_job_names(str(work_tree / ".github" / "workflows")))
    folder = Path(tempfile.mkdtemp(prefix="orch-it-tree-"))
    try:
        (folder / "workflows").mkdir(exist_ok=True)  # the folder is new from mkdtemp: nothing to refuse
        try:
            (folder / exceptions_list.FILE_NAME).write_bytes(
                _git_output(work_tree, "cat-file", "blob", f"{rev}:{exceptions_list.FILE_NAME}"))
        except ScanError:  # the commit has no such file
            pass
        listing = _git_output(work_tree, "ls-tree", "-r", "--name-only", "-z", rev, ".github/workflows")
        for name in filter(None, listing.decode("utf-8", "replace").split("\x00")):
            if name.endswith((".yml", ".yaml")):
                (folder / "workflows" / Path(name).name).write_bytes(_git_output(work_tree, "cat-file", "blob", f"{rev}:{name}"))
        names = pattern_kinds.read_job_names(str(folder / "workflows"))
        try:
            loaded, error = exceptions_list.load_exceptions(folder / exceptions_list.FILE_NAME), None
        except exceptions_list.ExceptionsError as e:  # raised when the list is asked for, not before
            loaded, error = None, e

        def load():
            if error is not None:
                raise error
            return loaded

        return load, names
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _repo_path(work_tree: Path, file: Path) -> str:
    """`file` as a path inside `work_tree`, else empty (a file outside the repo is never scanned)."""

    try:
        return file.resolve().relative_to(work_tree.resolve()).as_posix()
    except (ValueError, OSError):
        return ""


def _read_words(words_file: Path | None) -> tuple[str, ...]:
    if words_file is None:
        chosen = settings.load().get("private_words_file")
        if chosen is not None and not isinstance(chosen, str):
            raise settings.SettingsError("bad_setting", f"private_words_file: {chosen!r} is no path")
        words_file = Path(chosen) if chosen else settings.data_folder() / "private_words.txt"
    try:
        text = Path(words_file).read_text("utf-8-sig")
    except FileNotFoundError:
        return ()
    except UnicodeDecodeError:
        raise ScanError("words_unreadable", f"{words_file}: not UTF-8 text") from None
    except OSError as e:
        raise ScanError("words_unreadable", f"{words_file}: {e.strerror or type(e).__name__}") from None
    lines = (line.strip() for line in text.splitlines())
    return tuple(dict.fromkeys(line for line in lines if line and not line.startswith("#")))


# --- git ------------------------------------------------------------------------------------

# a log shows no signature lines and quotes no non-ASCII path; a diff has the prefixes the
# header reader expects, no renames, no external diff and no text conversion
_GIT_COMMAND = ("git",)
_GIT_CONFIG = ("-c", "core.quotePath=false", "-c", "log.showSignature=false")
# git looks where the work tree says, not where the caller's environment points (a hook runs
# with GIT_DIR set), and says its errors in one language
_GIT_ENV_DROPPED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")
_BAD_REVISION = re.compile(
    r"bad revision|unknown revision|ambiguous argument|needed a single revision|not a valid object"
    r"|must come before non-option",
    re.IGNORECASE)
_SHOW = ("show", "--format=%x00COMMIT%x00%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x00ENDMSG",
         "--unified=0", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
         "--src-prefix=a/", "--dst-prefix=b/")
# commits per `git show`: the command line stays short on every OS and a process is spent on
# a hundred commits, not on one
_BATCH = 100
_BINARY_PROBE = 8000


def _failure(errors) -> ScanError:
    errors.seek(0)
    message = errors.read().decode("utf-8", "replace").strip().splitlines()
    return ScanError("git_failed", message[0][:300] if message else "git failed")


def _git_env() -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name not in _GIT_ENV_DROPPED}
    return env | {"LC_ALL": "C"}


class _Running:
    """A running git, read through methods that note each bit of progress: the watchdog kills
    a git that makes none for `GIT_TIMEOUT_SECONDS`."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.stdin = proc.stdin
        self.stdout = proc.stdout
        self.last_progress = time.monotonic()
        self.timed_out = False

    def touch(self):
        self.last_progress = time.monotonic()

    def lines(self) -> Iterator[bytes]:
        for line in self.stdout:
            self.last_progress = time.monotonic()
            yield line

    def readline(self) -> bytes:
        line = self.stdout.readline()
        self.touch()
        return line

    def read(self, size: int) -> bytes:
        data = self.stdout.read(size)
        self.touch()
        return data

    def read_all(self) -> bytes:
        chunks = []
        while chunk := self.stdout.read1(65536):
            chunks.append(chunk)
            self.touch()
        return b"".join(chunks)


def _watch(running: _Running, done: threading.Event):
    while not done.wait(max(GIT_TIMEOUT_SECONDS / 4, 0.05)):
        if time.monotonic() - running.last_progress > GIT_TIMEOUT_SECONDS:
            running.timed_out = True
            running.proc.kill()
            return


@contextlib.contextmanager
def _process(work_tree: Path, *args: str, feed: bool = False):
    """A running `git -C <work_tree> <args>` for the caller to read (and write when `feed`). A git
    that makes no progress for `GIT_TIMEOUT_SECONDS` is killed (`ScanError("git_timeout")`); on
    a clean leave git must have exited 0, else `ScanError("git_failed")`."""

    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.Popen([*_GIT_COMMAND, "-C", str(work_tree), *_GIT_CONFIG, *args],
                                    stdin=subprocess.PIPE if feed else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=errors, env=_git_env(),
                                    creationflags=NO_WINDOW)
        except OSError:
            raise ScanError("git_failed", "git cannot be run") from None
        running = _Running(proc)
        done = threading.Event()
        watchdog = threading.Thread(target=_watch, args=(running, done), daemon=True)
        watchdog.start()
        timeout = ScanError("git_timeout", f"git made no progress for {GIT_TIMEOUT_SECONDS} seconds")
        try:
            yield running
            if proc.stdin:
                proc.stdin.close()
            running.read_all()
            code = proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            if running.timed_out:
                raise timeout from None
            raise
        finally:
            done.set()
            watchdog.join()
            for stream in (proc.stdin, proc.stdout):
                if stream:
                    with contextlib.suppress(OSError, ValueError):
                        stream.close()
        if running.timed_out:
            raise timeout
        if code != 0:
            raise _failure(errors)


def _git_output(work_tree: Path, *args: str) -> bytes:
    with _process(work_tree, *args) as running:
        return running.read_all()


def _unknown_rev(error: ScanError, what: str) -> ScanError:
    """`bad_rev` when git said it does not know the revision; any other failure as it was."""

    if error.code == "git_failed" and _BAD_REVISION.search(str(error)):
        return ScanError("bad_rev", f"{what} is not one git knows")
    return error


def commits_of_revs(work_tree: Path, *revs: str) -> list[str]:
    """The commits `git rev-list` names for `revs`, oldest first, as full shas. The caller has
    checked `revs` (full shas, `--not`, `--remotes=<name>`): none of it comes from a person."""

    try:
        out = _git_output(work_tree, "rev-list", "--reverse", *revs)
    except ScanError as e:
        raise _unknown_rev(e, "the revisions") from None
    return out.decode("ascii", "replace").split()


def commits_of_range(work_tree: Path, rev_range: str) -> list[str]:
    """The commits of `rev_range` (`A..B`, a branch, `HEAD`), oldest first, as full shas."""

    try:
        out = _git_output(work_tree, "rev-list", "--reverse", "--end-of-options", rev_range)
    except ScanError as e:
        raise _unknown_rev(e, "the range") from None
    return out.decode("ascii", "replace").split()


# a message is read between its NULs and the end marker, so any text of a body is read whole
_MESSAGES = ("log", "--no-walk=unsorted", "--format=%x00COMMIT%x00%H%x00%B%x00ENDMSG")
_MESSAGE = re.compile(rb"\x00COMMIT\x00([0-9a-f]{40,64})\x00(.*?)\x00ENDMSG", re.DOTALL)


def messages_of_commits(work_tree: Path, commits: list[str]):
    """Yields (full sha, message) of each commit in `commits` (full shas, as `commits_of_revs` gives
    them), in the order given, a batch of `_BATCH` commits per `git log`. A batch read before a git
    failure has been yielded already; the failure is raised as `ScanError` at the batch it reached.
    Nothing is asked of git for no commit (git would then read HEAD)."""

    for start in range(0, len(commits), _BATCH):
        out = _git_output(work_tree, *_MESSAGES, "--end-of-options", *commits[start:start + _BATCH])
        for m in _MESSAGE.finditer(out):
            yield m.group(1).decode("ascii", "replace"), m.group(2).decode("utf-8", "replace")


def emails_of_commits(work_tree: Path, commits: Iterable[str]) -> list[tuple[str, str, str]]:
    """(sha, author e-mail, committer e-mail) of each commit, in the order given, a hundred commits
    per git call. Nothing is asked of git for no commit (git would then read HEAD)."""

    shas = list(commits)
    found: list[tuple[str, str, str]] = []
    for start in range(0, len(shas), _BATCH):
        batch = shas[start:start + _BATCH]
        out = _git_output(work_tree, "log", "--no-walk=unsorted", "--format=%H%x00%ae%x00%ce", *batch)
        for line in out.decode("utf-8", "replace").splitlines():
            sha, author, committer = line.split("\x00")
            found.append((sha, author, committer))
    return found


def _unquote(text: str) -> tuple[str, int]:
    """The C-style quoted string git writes a path in, starting at `text[0] == '"'`: the path
    and the index after the closing quote."""

    escapes = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}
    out = bytearray()
    i = 1
    while i < len(text) and text[i] != '"':
        char = text[i]
        if char != "\\":
            out += char.encode("utf-8")
        elif text[i + 1:i + 2] in escapes:
            out.append(escapes[text[i + 1]])
            i += 1
        elif re.fullmatch(r"[0-7]{3}", text[i + 1:i + 4]):
            out.append(int(text[i + 1:i + 4], 8) & 0xFF)
            i += 3
        else:
            out += b"\\"
        i += 1
    return out.decode("utf-8", "replace"), i + 1


def _path_of_header(line: str) -> str:
    """The path of a file's `diff --git a/P b/P` or `diff --cc P` header (no renames: both
    sides of the first form are the same path)."""

    rest = line.split(" ", 2)[2]
    if line.startswith("diff --git "):
        if rest.startswith('"'):
            return _unquote(rest)[0][2:]
        size = (len(rest) - 5) // 2
        if len(rest) == 2 * size + 5 and rest[2:2 + size] == rest[size + 5:]:
            return rest[2:2 + size]
        return rest[2:rest.rfind(" b/")]
    return _unquote(rest)[0] if rest.startswith('"') else rest


_HUNK = re.compile(r"@@+ (?:-\d+(?:,\d+)? )+\+(\d+)(?:,\d+)? @@")


class _CommitScan:
    """The hits of the commits of one `git show` stream, fed its lines in order."""

    def __init__(self, patterns: Patterns):
        self.patterns = patterns
        self.hits: list[Hit] = []
        self.sha = ""
        self.in_message = False
        self.message_line = 0
        self.path: str | None = None
        self.real: str | None = None  # the path of the file in the diff, kept after `path` is flushed
        self.added: dict[tuple[str, str], set[int]] = {}  # (sha, path) -> the new lines it adds
        self.shown: str | None = None
        self.in_hunk = False
        self.columns = 1
        self.new_line = 0

    def _add(self, text: str, where: str, file: str | None = None, line: int | None = None):
        for kind in self.patterns.find(text):
            self.hits.append(Hit(kind, where, self.sha, file, line))

    def _flush_path(self):
        if self.path is not None:
            self._add(self.path, "path", self.shown)
            self.path = None

    def _message(self, text: str):
        self.message_line += 1
        self._add(text, "message", None, self.message_line)

    def feed(self, raw: bytes):
        line = raw.decode("utf-8", "replace")
        if self.in_message:
            body, ended, _ = line.partition("\x00ENDMSG")
            if body.rstrip("\n") or not ended:
                self._message(body.rstrip("\n"))
            self.in_message = not ended
        elif raw.startswith(b"\x00COMMIT\x00"):
            self._flush_path()
            self.in_hunk = False
            _, _, self.sha, an, ae, cn, ce, first = line.split("\x00", 7)
            self._add(f"{an} <{ae}>", "author")
            self._add(f"{cn} <{ce}>", "committer")
            self.message_line = 0
            self.in_message = True
            self.feed_first_message_line(first)
        elif self.in_hunk and line[:1] in "+- \\":
            self._hunk_line(line)
        elif line.startswith("diff "):
            self._flush_path()
            self.in_hunk = False
            self.path = self.real = _path_of_header(line.rstrip("\n"))
            self.shown = self.patterns.redact(self.path)
        elif line.startswith("@@"):
            self._flush_path()
            found = _HUNK.match(line)
            if found:
                self.in_hunk = True
                self.columns = line.index(" ") - 1  # one column per parent
                self.new_line = int(found.group(1))
        elif line.startswith("deleted file mode"):
            self.path = None
        elif line.startswith(("Binary files", "GIT binary patch")):
            self._flush_path()

    def feed_first_message_line(self, first: str):
        body, ended, _ = first.partition("\x00ENDMSG")
        self._message(body.rstrip("\n"))
        self.in_message = not ended

    def _hunk_line(self, line: str):
        marks = line[:self.columns]
        if marks[:1] == "\\" or "-" in marks:
            return
        if marks == "+" * self.columns:
            self._add(line[self.columns:].rstrip("\n"), "content", self.shown, self.new_line)
            if self.patterns.values is not None and self.real is not None:
                self.added.setdefault((self.sha, self.real), set()).add(self.new_line)
        self.new_line += 1

    def finish(self) -> list[Hit]:
        self._flush_path()
        return self.hits


def scan_commits(work_tree: Path, commits: Iterable[str], patterns: Patterns) -> list[Hit]:
    """The private data each commit adds: lines it adds (a merge: only those new against every
    parent), paths it adds or changes, its message, its author and its committer. Commits
    go through `git show` a hundred at a time and each stream is read once, line by line."""

    shas = list(commits)
    hits: list[Hit] = []
    for start in range(0, len(shas), _BATCH):
        scan = _CommitScan(patterns)
        with _process(work_tree, *_SHOW, "--end-of-options", *shas[start:start + _BATCH]) as git:
            for raw in git.lines():
                scan.feed(raw)
        hits += scan.finish()
        if patterns.values is not None:
            hits += _file_hits(work_tree, scan.added, patterns)
    return hits


def _file_hits(work_tree: Path, added: dict[tuple[str, str], set[int]], patterns: Patterns) -> list[Hit]:
    """The hits of `Patterns.find_in_file` on the lines each (sha, path) of `added` adds: a pattern
    kind is judged on the whole file as that commit has it, and only its added lines are kept."""

    wanted = [(key, lines) for key, lines in added.items() if "\n" not in key[1]]
    hits: list[Hit] = []
    if not wanted:
        return hits
    with _process(work_tree, "cat-file", "--batch", feed=True) as git:
        for start in range(0, len(wanted), _BATCH):
            chunk = wanted[start:start + _BATCH]
            git.stdin.write("".join(f"{sha}:{path}\n" for (sha, path), _ in chunk).encode("utf-8"))
            git.stdin.flush()
            for (sha, path), lines in chunk:
                header = git.readline().rstrip(b"\r\n")
                if header.endswith(b" missing"):
                    continue
                parts = header.split()
                if len(parts) != 3 or parts[1] != b"blob":
                    raise ScanError("git_failed", f"{sha[:12]}:{path} cannot be read")
                data = git.read(int(parts[2]))
                git.read(1)
                if b"\x00" in data[:_BINARY_PROBE]:
                    continue
                shown = patterns.redact(path)
                hits += [Hit(kind, "content", sha, shown, number)
                         for kind, number in patterns.find_in_file(path, data.decode("utf-8", "replace"))
                         if number in lines]
    return hits


def scan_tree(work_tree: Path, patterns: Patterns, rev: str = "HEAD") -> list[Hit]:
    """The private data in every tracked file of `rev`: its path and each line of its content
    (a binary file: its path only). `Hit.commit` is the sha `rev` names."""

    try:
        sha = _git_output(work_tree, "rev-parse", "--verify", "--end-of-options",
                          f"{rev}^{{commit}}").decode("ascii", "replace").strip()
    except ScanError as e:
        raise _unknown_rev(e, "the revision") from None
    listing = _git_output(work_tree, "ls-tree", "-r", "-z", "--full-tree", sha)
    files = []
    for entry in filter(None, listing.split(b"\x00")):
        meta, _, path = entry.partition(b"\t")
        mode, kind, oid = meta.decode("ascii", "replace").split()
        if kind == "blob":
            files.append((oid, path.decode("utf-8", "replace")))
    hits: list[Hit] = []

    def add(text: str, where: str, file: str, line: int | None = None):
        for kind in patterns.find(text):
            hits.append(Hit(kind, where, sha, file, line))

    with _process(work_tree, "cat-file", "--batch", feed=True) as git:
        for start in range(0, len(files), _BATCH):
            chunk = files[start:start + _BATCH]
            git.stdin.write("".join(f"{oid}\n" for oid, _ in chunk).encode("ascii"))
            git.stdin.flush()
            for oid, path in chunk:
                header = git.readline().split()
                if len(header) != 3 or header[1] != b"blob":
                    raise ScanError("git_failed", f"object {oid} cannot be read")
                data = git.read(int(header[2]))
                git.read(1)
                shown = patterns.redact(path)
                add(path, "path", shown)
                if b"\x00" in data[:_BINARY_PROBE]:
                    continue
                text = data.decode("utf-8", "replace")
                for number, line in enumerate(text.split("\n"), 1):
                    if line:
                        add(line, "content", shown, number)
                hits += [Hit(kind, "content", sha, shown, number) for kind, number in patterns.find_in_file(path, text)]
    return hits
