"""The exceptions list of #413 unit 2: `guards-exceptions.toml`, the one list where a finding of `kinds.py` is allowed
on purpose. An entry is an `[[exception]]` table with `file` (a glob, or a repo path), `pattern` (a regex over the
line) and `reason` (text, required). An entry with an empty or missing reason fails on its own, naming the entry.

`is_allowed_place` is the policy for the places where a literal needs no entry: the settings file, and anything under
`ALLOWED_PLACES`. Paths are repo-relative; a backslash is read as a slash."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

FILE_NAME = "guards-exceptions.toml"
ALLOWED_PLACES = ("docs/research/", "docs/adr/")  # the records: research and ADRs, whose files may hold a literal


class ExceptionsError(Exception):
    """An exceptions file, or one of its entries, that cannot be used. The message names the entry at fault."""


@dataclass(frozen=True)
class _Entry:
    file: re.Pattern  # the entry's glob, compiled to a regex over a repo-relative path
    pattern: re.Pattern  # the entry's regex over the line
    reason: str


@dataclass(frozen=True)
class Exceptions:
    """The entries of one exceptions file; `allows` is the question asked of them."""

    entries: tuple[_Entry, ...] = ()

    def allows(self, path: str, line_text: str) -> bool:
        """True when an entry's file matches `path` and its pattern finds in `line_text`."""

        path = _posix(path)
        return any(entry.file.fullmatch(path) and entry.pattern.search(line_text) for entry in self.entries)


def load_exceptions(path: str | Path) -> Exceptions:
    """The entries of the TOML file at `path`; a file that does not exist holds none. An entry that cannot be used
    (no file, no pattern, a pattern that does not compile, an empty or missing reason) raises `ExceptionsError`."""

    file = Path(path)
    if not file.exists():
        return Exceptions()
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:  # a TOML error and a decoding error are both ValueErrors
        raise ExceptionsError(f"{file}: not readable as TOML: {error}") from error
    tables = data.get("exception", [])
    if not isinstance(tables, list):
        raise ExceptionsError(f"{file}: the entries are [[exception]] tables")
    return Exceptions(tuple(_entry(number, table) for number, table in enumerate(tables, 1)))


def is_allowed_place(path: str, settings_file: str) -> bool:
    """True for the settings file itself and for anything under `ALLOWED_PLACES`: a literal there is no finding."""

    path = _posix(path)
    return path == _posix(settings_file) or any(path.startswith(place) for place in ALLOWED_PLACES)


def _entry(number: int, table: dict) -> _Entry:
    name = f"[[exception]] entry {number} (file {table.get('file')!r})"
    glob = table.get("file")
    if not isinstance(glob, str) or not glob.strip():
        raise ExceptionsError(f"{name}: no file: a glob or a repo path is required")
    pattern = table.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ExceptionsError(f"{name}: no pattern: a regex over the line is required")
    reason = table.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ExceptionsError(f"{name}: empty or missing reason: each entry says why it is allowed")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise ExceptionsError(f"{name}: pattern does not compile: {error}") from error
    return _Entry(re.compile(_glob_regex(_posix(glob))), compiled, reason.strip())


def _glob_regex(glob: str) -> str:
    """A glob as a regex: `**/` any folders, `**` anything, `*` a name's part, `?` one character."""

    out, index = [], 0
    while index < len(glob):
        if glob.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif glob.startswith("**", index):
            out.append(".*")
            index += 2
        elif glob[index] == "*":
            out.append("[^/]*")
            index += 1
        elif glob[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(glob[index]))
            index += 1
    return "".join(out)


def _posix(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")
