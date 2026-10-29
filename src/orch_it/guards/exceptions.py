"""The exceptions list of #413 unit 2: `guards-exceptions.toml`, the one list where a finding is allowed on purpose.
An entry is an `[[exception]]` table with `file` (a glob or a repo path, or a list of them), `pattern` (a regex over
the line), `kinds` (the kinds of finding it excuses, a non-empty list of `EXCUSABLE_KINDS`: an entry is never a
blanket over a line) and `reason` (text, required). An entry with no kinds, an unknown kind or an empty or missing
reason fails on its own, naming the entry.

`is_allowed_place` is the policy for the places where a literal needs no entry: the settings file, and anything under
`ALLOWED_PLACES`. Paths are repo-relative; a backslash is read as a slash."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from orch_it import language
from orch_it.guards import kinds as pattern_kinds
from orch_it.guards import values as setup_values

FILE_NAME = "guards-exceptions.toml"
ALLOWED_PLACES = ("docs/research/", "docs/adr/")  # the records: research and ADRs, whose files may hold a literal
# the kinds an entry may name: those of `Patterns.find_in_file`; the setup's home and host are judged by `find`,
# line by line, and no entry reaches them
EXCUSABLE_KINDS = tuple(kind for kind in (*setup_values.KINDS, *pattern_kinds.KINDS) if kind not in ("home", "host"))


class ExceptionsError(Exception):
    """An exceptions file, or one of its entries, that cannot be used. The message is the sentence of the
    table `exc_*` for the refusal, in the setting's language, and names the entry at fault. `code` is the
    stable word of the refusal for callers: `not_toml`, `not_tables`, `no_file`, `no_pattern`, `kinds`,
    `no_reason`, `bad_pattern`; `unreadable` when a caller gives only a message."""

    def __init__(self, message: str, code: str = "unreadable"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Entry:
    file: re.Pattern  # the entry's glob, compiled to a regex over a repo-relative path
    pattern: re.Pattern  # the entry's regex over the line
    kinds: frozenset[str]  # the kinds of finding the entry excuses, no other
    reason: str


@dataclass(frozen=True)
class Exceptions:
    """The entries of one exceptions file; `allows` is the question asked of them."""

    entries: tuple[_Entry, ...] = ()

    def allows(self, path: str, line_text: str, kind: str) -> bool:
        """True when an entry that names `kind` has a file matching `path` and a pattern finding in `line_text`."""

        path = _posix(path)
        return any(kind in entry.kinds and entry.file.fullmatch(path) and entry.pattern.search(line_text)
                   for entry in self.entries)


def load_exceptions(path: str | Path) -> Exceptions:
    """The entries of the TOML file at `path`; a file that does not exist holds none. An entry that cannot be used
    (no file, no pattern, a pattern that does not compile, no kinds or a kind not in `EXCUSABLE_KINDS`, an empty or
    missing reason) raises `ExceptionsError`."""

    file = Path(path)
    if not file.exists():
        return Exceptions()
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:  # a TOML error and a decoding error are both ValueErrors
        raise _refused("not_toml", "exc_not_toml", file=file, error=error) from error
    tables = data.get("exception", [])
    if not isinstance(tables, list):
        raise _refused("not_tables", "exc_not_tables", file=file)
    return Exceptions(tuple(_entry(number, table) for number, table in enumerate(tables, 1)))


def is_allowed_place(path: str, settings_file: str) -> bool:
    """True for the settings file itself and for anything under `ALLOWED_PLACES`: a literal there is no finding."""

    path = _posix(path)
    return path == _posix(settings_file) or any(path.startswith(place) for place in ALLOWED_PLACES)


def _refused(code: str, key: str, **fields: object) -> ExceptionsError:
    """The refusal `code`, its sentence the table's `key` in the setting's language (the words of the setting's
    table, or the refusal naming the missing table)."""

    return ExceptionsError(language.text_or_refusal(key, **fields), code)


def _entry(number: int, table: dict) -> _Entry:
    def refuse(code: str, key: str, **fields: object) -> ExceptionsError:
        name = language.text_or_refusal("exc_entry", number=number, file=repr(table.get("file")))
        return _refused(code, key, name=name, **fields)

    globs = table.get("file")
    if isinstance(globs, str):
        globs = [globs]
    if not isinstance(globs, list) or not globs or not all(isinstance(glob, str) and glob.strip() for glob in globs):
        raise refuse("no_file", "exc_no_file")
    pattern = table.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise refuse("no_pattern", "exc_no_pattern")
    kinds = table.get("kinds")
    if (not isinstance(kinds, list) or not kinds or not all(isinstance(kind, str) for kind in kinds)
            or not set(kinds) <= set(EXCUSABLE_KINDS)):
        raise refuse("kinds", "exc_kinds", kinds=", ".join(EXCUSABLE_KINDS))
    reason = table.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise refuse("no_reason", "exc_no_reason")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise refuse("bad_pattern", "exc_bad_pattern", error=error) from error
    files = "|".join(f"(?:{_glob_regex(_posix(glob))})" for glob in globs)
    return _Entry(re.compile(files), compiled, frozenset(kinds), reason.strip())


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
