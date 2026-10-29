"""The values that differ per machine and per account, read at run time and found in a text (#413).

A value that varies is never a literal in code, tests or tickets (CODING_STANDARDS.md). This
module is the "read at run time" half of the check that refuses one written as a literal:
`read_values` asks the setup it runs on (a machine, or a GitHub Actions job) for each kind, and
`find_in` finds those values in a text in every spelling. Nothing is listed in a file: a runner's
home folder is found because the runner says it, not because someone wrote it down.

Every read goes through an argument, so a test supplies invented values and touches nothing real:
`env` (a mapping, default `os.environ`), `run` (a command list -> its output, default `default_run`,
which raises when the command fails), `home` and `host` (default this machine's; `""` says the
machine has none). A failed or empty read is an absent value, listed in `Values.not_checked`.

`Values.render_not_checked()` is the line a result prints beside itself. The callers (the scanner
wiring, #413 unit 5) print it and must never print a clean result without it when it is not empty.

A finding carries the kind, the line and the first and last character of the text matched, never the
value (and `Values` hides its values from `repr`).

Reference forms (#415 unit 7). No doc under `docs/` describes `guards check-text` yet, so this is where its
unjudged locations are listed. The check's callers (`guards check-text`, the text hook, the push scan and the
ticket shape check) all come through `find_in`, which blanks the span of a reference to one GitHub object
before it looks for a value. Allowed, so no finding:
- `https://github.com/<owner>/<repo>/issues/<digits>`, `/pull/<digits>`, `/commit/<7-40 hex>`; what follows the
  number or the hash (`#issuecomment-...`, `/files`) is ordinary text and is judged as before;
- the short forms `<owner>/<repo>#<digits>` and `<owner>/<repo>@<7-40 hex>`, when not part of a longer path.
Still judged, a finding for each value in it: the repo root `https://github.com/<owner>/<repo>`, a clone URL
(`...<repo>.git`, `git@github.com:<owner>/<repo>`), `/tree/...` and `/blob/...` links, `raw.githubusercontent.com/...`,
an issue with no number or a template (`/issues/{n}`), and a bare account name in prose, also beside an allowed
link (on its own line or its own span).
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
from dataclasses import dataclass, field, fields
from pathlib import Path

from orch_it import language

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
COMMAND_SECONDS = 20

KINDS = ("account", "repo", "host", "home", "user", "projects_folder", "git_name", "git_email")
_PATH_KINDS = ("home", "projects_folder")
_MIN_LENGTH = 3

NOT_SET = "not set"
TOO_SHORT = "too short to scan"
# the table key of each reason `not_checked` stores (the reasons stay these English words in the data); a reason
# with no key is written as it is
_REASON_KEYS = {NOT_SET: "not_checked_not_set", TOO_SHORT: "not_checked_too_short"}


def _reason_text(reason: str) -> str:
    key = _REASON_KEYS.get(reason)
    return language.text_or_refusal(key) if key is not None else reason

_SEP = r"[\\/]+"  # `/`, `\`, and the doubled `\\` of a string literal
# `github.com` may carry an SSH host alias (`github.com-work`) before the `:` or `/`
_ORIGIN = re.compile(r"(?:^|[@/])github\.com[^:/\s]*[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?\s*$")
_DRIVE = re.compile(r"([A-Za-z]):(?:[\\/]+(.*))?$")


def default_run(cmd: list[str], timeout: float = COMMAND_SECONDS) -> str:
    """The output of `cmd`, run without a window and cut after `timeout` seconds; raises when it cannot run, fails
    or is cut."""

    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                          check=True)
    return done.stdout


@dataclass(frozen=True)
class Values:
    """The values read, one field per kind: a tuple of the values to scan for, or None when the
    setup has none. `not_checked`: (kind, reason) for each kind that is absent (`not set`) or has a
    value of fewer than three characters (`too short to scan`), which is never scanned."""

    account: tuple[str, ...] | None = field(default=None, repr=False)
    repo: tuple[str, ...] | None = field(default=None, repr=False)
    host: tuple[str, ...] | None = field(default=None, repr=False)
    home: tuple[str, ...] | None = field(default=None, repr=False)
    user: tuple[str, ...] | None = field(default=None, repr=False)
    projects_folder: tuple[str, ...] | None = field(default=None, repr=False)
    git_name: tuple[str, ...] | None = field(default=None, repr=False)
    git_email: tuple[str, ...] | None = field(default=None, repr=False)
    not_checked: tuple[tuple[str, str], ...] = ()

    def render_not_checked(self) -> str:
        """The line a result prints beside it, `not checked: git_email (not set), ...`; empty when
        every kind was checked. A clean result is never printed without this line."""

        if not self.not_checked:
            return ""
        items = ", ".join(
            language.text_or_refusal("not_checked_item", kind=kind, reason=_reason_text(reason))
            for kind, reason in self.not_checked)
        return language.text_or_refusal("not_checked_line", items=items)


@dataclass(frozen=True)
class Finding:
    """One value found: its `kind`, `redacted` (the first and last character of the text matched)
    and `line` (1-based)."""

    kind: str
    redacted: str
    line: int


def read_values(*, env=None, run=None, home=None, host=None) -> Values:
    """The values of the setup this runs on. On GitHub Actions (`GITHUB_ACTIONS=true`) the runner's
    own environment adds the repo, owner, actor, home, user and computer name. Never raises: a read
    that fails is an absent value."""

    real_env = env is None
    env = os.environ if env is None else env
    run = run or default_run
    actions = env.get("GITHUB_ACTIONS") == "true"
    raw: dict[str, list[str]] = {kind: [] for kind in KINDS}

    def ask(*cmd: str) -> str:
        try:
            return run(list(cmd)).strip()
        except Exception:  # a command that cannot run or fails is a value this setup does not have
            return ""

    if home is None:
        try:
            home = str(Path.home())
        except (RuntimeError, OSError):
            home = ""
    if host is None:
        try:
            host = socket.gethostname()
        except OSError:
            host = ""
    raw["home"].append(home)
    raw["host"] += [host, host.split(".")[0]]
    raw["user"] += [env.get(name, "") for name in ("USERNAME", "USER", "LOGNAME")]
    if real_env and not any(raw["user"]):
        import getpass
        try:
            raw["user"].append(getpass.getuser())
        except Exception:  # no user name in the environment or the password database
            pass

    origin = _ORIGIN.search(ask("git", "remote", "get-url", "origin"))
    if origin:
        raw["repo"].append("/".join(origin.groups()))
        raw["account"].append(origin.group(1))
    raw["account"].append(ask("gh", "api", "user", "--jq", ".login"))
    common = ask("git", "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        checkout = _parent(common) if re.search(r"[\\/]\.git[\\/]*$", common) else common
        raw["projects_folder"].append(_parent(checkout))
    raw["git_name"].append(ask("git", "config", "user.name"))
    raw["git_email"].append(ask("git", "config", "user.email"))

    if actions:
        raw["repo"].append(env.get("GITHUB_REPOSITORY", ""))
        raw["account"] += [env.get("GITHUB_REPOSITORY_OWNER", ""), env.get("GITHUB_ACTOR", "")]
        raw["account"] = [a for a in raw["account"] if not a.endswith("[bot]")]  # not a person's
        raw["home"] += [env.get("HOME", ""), env.get("USERPROFILE", "")]
        if env.get("RUNNER_TEMP"):
            raw["home"].append(_parent(env["RUNNER_TEMP"]))
        raw["host"].append(env.get("COMPUTERNAME", ""))
    return _collect(raw)


def _parent(path: str) -> str:
    return re.sub(r"[\\/]+[^\\/]*[\\/]*$", "", path.strip())


def _collect(raw: dict[str, list[str]]) -> Values:
    found: dict[str, tuple[str, ...] | None] = {}
    not_checked = []
    for kind in KINDS:
        wanted = [v for v in dict.fromkeys(v.strip() for v in raw[kind] if v) if v]
        if kind == "host":
            wanted = [v for v in wanted if v.lower() != "localhost"]
        usable = tuple(v for v in wanted if _pattern(kind, v) is not None)
        found[kind] = usable or None
        if not wanted:
            not_checked.append((kind, NOT_SET))
        elif len(usable) < len(wanted) and (not usable or kind != "host"):
            not_checked.append((kind, TOO_SHORT))
    return Values(**found, not_checked=tuple(not_checked))


def _pattern(kind: str, value: str) -> re.Pattern | None:
    """The regex of one value in every spelling, or None when it is too short to scan."""

    if len(value) < _MIN_LENGTH:
        return None
    if kind in _PATH_KINDS:
        return _path_pattern(value)
    if kind == "user":
        return _user_pattern(value)
    words = r"\s+".join(re.escape(word) for word in value.split())
    return re.compile(rf"(?<![A-Za-z0-9]){words}(?![A-Za-z0-9])", re.IGNORECASE)


def _user_pattern(value: str) -> re.Pattern:
    """A user name is often an ordinary word (`user`, `runner`), so it matches only where it names
    an identity: as a path component (the name between two slashes, or last in a path), before
    `@host`, or as the value of `USER=`, `RUNNER_USER: `, `user=`, `user.name=` or `--user `. The
    matched value is group `v`."""

    name = re.escape(value)
    quote = "[\"']?"
    after = (r"(?<=[\\/])"
             rf"|(?<![A-Za-z0-9])(?:[A-Za-z_]*user(?:name)?|user\.name|logname){quote}\s*[=:]\s*{quote}"
             rf"|--user(?:\s+|=){quote}")
    before_host = rf"(?<![A-Za-z0-9_.-])(?={name}@[A-Za-z0-9])"
    return re.compile(rf"(?:{after}|{before_host})(?P<v>{name})(?![A-Za-z0-9_-])", re.IGNORECASE)


def _path_pattern(value: str) -> re.Pattern | None:
    drive = _DRIVE.match(value)
    parts = [part for part in re.split(_SEP, (drive.group(2) or "") if drive else value) if part]
    if not parts:
        return None
    body = _SEP.join(re.escape(part) for part in parts)
    if drive:
        letter = drive.group(1)
        body = rf"(?:{letter}:|/{letter}|/cygdrive/{letter}|/mnt/{letter}){_SEP}{body}"
    elif value.startswith(("/", "\\")):
        body = _SEP + body
    return re.compile(rf"(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_-])", re.IGNORECASE if drive else 0)


# a reference to one GitHub object (#415 unit 7): the same object from every setup, so its span is no place to
# look for a value; what follows the number or the hash is ordinary text, a link that is not one of these is not
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]*)"
_REPO = r"[A-Za-z0-9_.-]+"
_HASH = r"[0-9a-fA-F]{7,40}(?![0-9a-fA-F])"
# accepted gap: a relative path `dir/<value>#3` is a short reference too; `owner/repo#N` cannot tell them apart
_REFERENCE = re.compile(
    rf"https://github\.com/{_OWNER}/{_REPO}/(?:issues|pull)/[0-9]+"
    rf"|https://github\.com/{_OWNER}/{_REPO}/commit/{_HASH}"
    rf"|(?<![A-Za-z0-9_./\\:@#-]){_OWNER}/{_REPO}(?:#[0-9]+|@{_HASH})")


def _without_references(text: str) -> str:
    """`text` with the span of each reference to a GitHub object blanked to spaces, the lines kept."""

    return _REFERENCE.sub(lambda match: " " * len(match.group()), text)


def find_in(text: str, values: Values) -> list[Finding]:
    """Every value of `values` found in `text`, in order of line. Words match in any case, bounded
    by non-alphanumerics; paths in every slash spelling, in any case when they start with a drive.
    The span of a reference to one GitHub object is blanked first (`_REFERENCE`): an issue, pull request or
    commit link, `owner/repo#N` and `owner/repo@sha`; a repo root, a clone URL, a tree or blob link or an issue
    without a number is still judged."""

    patterns = [(kind, pattern) for kind in KINDS for value in getattr(values, kind) or ()
                if (pattern := _pattern(kind, value)) is not None]
    findings = []
    for number, line in enumerate(_without_references(text).split("\n"), 1):
        for kind, pattern in patterns:
            for match in pattern.finditer(line):
                matched = (match.group("v") if "v" in pattern.groupindex else match.group()).strip()
                findings.append(Finding(kind, f"{matched[0]}..{matched[-1]}", number))
    return findings
