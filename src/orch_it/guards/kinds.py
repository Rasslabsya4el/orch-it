"""The pattern kinds of #413 unit 2: the lines of a repo's text that break kinds 4, 5 and 6 of the coding standard
(a call that exists on one OS only, a bare `python`, a GitHub job name written as a literal).

`find_pattern_kinds` judges the text of a file, by its suffix: `.py`, `.md`, or any other file. `find_in_ticket_text`
judges a ticket body, markdown with or without fences. Neither reads a file. The job names come in as an argument;
`read_job_names` reads them from the workflow files (each job's `jobs:` key and its `name:` value).

The one-OS rule is a judgement on the text, not a proof. An OS-only call counts as guarded when an OS test
(`sys.platform`, `os.name`, `platform.system()`) sits within 15 lines above it in the same function, or the import is
inside a `try` with an `except ImportError`. That says the call sits in a branch on the OS. It does not prove that the
other OSes have a path to the same result (a known limit)."""

import re
from dataclasses import dataclass
from pathlib import Path

KINDS = ("bare_python", "one_os_call", "check_name_literal")
GUARD_WINDOW = 15  # the lines above an OS-only call in which its OS test may sit
TICKET_PATH = "<ticket>"  # the `path` of a finding in a ticket body

# (a) `python` or `python3` in command position: at a line start, after a separator, a keyword, a `run:` key or a
# JSON "command" key. A word after `=`, `/` or `$` is not a command word.
_SHELL_COMMAND = re.compile(
    r"""(?:^\s*|[;&|(`]\s*|\$\(\s*|\brun:\s*|"command"\s*:\s*"|\b(?:then|do|else|exec|if|elif|while|until)\s+)"""
    r"""(python3?)(?=[\s"';&|)`]|$)"""
)
_PY_ARGV = re.compile(r"""\[\s*["'](python3?)["']\s*[,\]]""")  # a list whose first element is the word
_PY_ARGV_FIRST = re.compile(r"""\s*["'](python3?)["']\s*[,\]]""")  # the same, the first element on its own line
_PY_COMMAND = re.compile(r"""\b(?:run|Popen|call|check_output|check_call|system)\(\s*["'](python3?)\s""")
# a ticket: the word at a line start with an option or a script after it (so a prose sentence that starts with
# the word is no command), after a shell prompt `$ `, after a backtick, or after `run:`
_TICKET_COMMAND = re.compile(
    r"""(?:^\s*(?=python3?\s+(?:-|\S*\.py\b))|\$\s+|`|\brun:\s*)(python3?)(?=[\s`'"]|$)"""
)

# (b) an OS-only call, and the OS test that guards it
_OS_CALL = re.compile(
    r"\b(?:ctypes\.windll|os\.startfile|os\.getuid|os\.fork)\b"
    r"|\b(?i:taskkill|tskill|Stop-Process|wmic)\b"  # the Windows-only process tools, any casing (CODING_STANDARDS.md kind 4)
    r"|\b(?:winreg|msvcrt|fcntl|termios)\b(?!\s*=[^=])"  # a bare name bound by `msvcrt = None` is no use of it
)
_OS_TEST = re.compile(r"\s*(?:if|elif)\s.*\b(?:sys\.platform|os\.name|platform\.system\()")
_DEF = re.compile(r"\s*(?:async\s+)?def\s")
_IMPORT = re.compile(r"\s*(?:import|from)\s")
_TRY = re.compile(r"\s*try:\s*(?:#.*)?$")
_IMPORT_ERROR = re.compile(r"\s*except\s*\(?\s*ImportError\b")

# the workflow reader: the `jobs:` keys, and each job's `name:` field
_JOBS_KEY = re.compile(r"jobs:\s*(?:#.*)?$")
_JOB_NAME = re.compile(r"([\w.-]+):\s*(?:#.*)?$")
_NAME_FIELD = re.compile(r"name:\s*(.+?)\s*(?:#.*)?$")


@dataclass(frozen=True)
class Finding:
    """One match. `kind` is one of `KINDS`; `line` is 1-based; `text` is the matched word (`python`, the call's
    name, the job name); `path` is the path as it was given, or `TICKET_PATH` for a ticket body."""

    kind: str
    line: int
    text: str
    path: str


def find_pattern_kinds(path: str, text: str, job_names: frozenset[str] = frozenset()) -> list[Finding]:
    """Every match in `text`, the text of the file at `path`, judged by the suffix of `path`. A `.py` file gets the
    subprocess forms of `bare_python`, `one_os_call` and `check_name_literal` (the names of `job_names` only). A
    `.md` file gets `bare_python` inside its code fences, where prose is no command. Any other file gets the shell
    forms of `bare_python`. A comment line is never a match."""

    lines = text.splitlines()
    if path.endswith(".py"):
        hits = [*_python_command_hits(lines), *_os_call_hits(lines), *_name_literal_hits(lines, job_names)]
    elif path.endswith(".md"):
        hits = list(_shell_hits(lines, _fenced_indexes(lines)))
    else:
        hits = list(_shell_hits(lines, range(len(lines))))
    return [Finding(kind, number, matched, path) for kind, number, matched in sorted(hits, key=_by_line)]


def find_in_ticket_text(text: str, job_names: frozenset[str] = frozenset()) -> list[Finding]:
    """Every match in a ticket body, markdown with or without fences: a bare `python` or `python3` in command position
    (see `_TICKET_COMMAND`), a one-OS name anywhere (a ticket has no OS test of its own, so each one is a finding),
    and a job name as a quoted or backticked literal. A `#` line is a heading here, not a comment. The path of every
    finding is `TICKET_PATH`."""

    lines = text.splitlines()
    hits = [*_ticket_python_hits(lines), *_os_name_hits(lines), *_quoted_name_hits(lines, job_names)]
    return [Finding(kind, number, matched, TICKET_PATH) for kind, number, matched in sorted(hits, key=_by_line)]


def find_in_record(path: str, text: str, job_names: frozenset[str] = frozenset()) -> list[Finding]:
    """Every match in a record's text: a file the value check judges in full (#415 unit 4 item 3), a `.md`, `.txt` or
    `.rst` file, a file under `docs/`, or a file whose suffix no source list names. It is `find_pattern_kinds` of its
    suffix, plus a one-OS call anywhere (a record has no OS test of its own) and a job name as a quoted or backticked
    literal, as a ticket body has them. `find_pattern_kinds` itself, which the scanner uses, is unchanged."""

    lines = text.splitlines()
    extra = [Finding(kind, number, matched, path)
             for kind, number, matched in sorted([*_os_name_hits(lines), *_quoted_name_hits(lines, job_names)],
                                                 key=_by_line)]
    found = {*find_pattern_kinds(path, text, job_names), *extra}
    return sorted(found, key=lambda finding: (finding.line, finding.kind))


def read_job_names(folder: str) -> frozenset[str]:
    """The names a check of the workflows in `folder` carries, read from the `*.yml` and `*.yaml` files: each job's
    `jobs:` key, and each job's `name:` value (what GitHub shows as the check). A value with a matrix suffix,
    `tests (os)`, also gives its bare part, `tests`. A value with a `${{` expression is no literal: it gives its bare
    part only when that part has no expression, as `tests (${{ matrix.os }})` gives `tests`."""

    names: set[str] = set()
    for pattern in ("*.yml", "*.yaml"):
        for workflow in Path(folder).glob(pattern):
            names |= _job_names(workflow.read_text(encoding="utf-8").splitlines())
    return frozenset(names)


def _by_line(hit):
    return hit[1], hit[0]


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _name_pattern(job_names: frozenset[str], quotes: str):
    """A regex for a job name between two of `quotes` (group 2 is the name), or None when there are no names."""

    if not job_names:
        return None
    alternatives = "|".join(re.escape(name) for name in sorted(job_names))
    return re.compile(rf"""([{quotes}])({alternatives})\1""")


def _python_command_hits(lines):
    for index, line in enumerate(lines):
        if _is_comment(line):
            continue
        for match in (*_PY_ARGV.finditer(line), *_PY_COMMAND.finditer(line)):
            yield "bare_python", index + 1, match.group(1)
        first = _PY_ARGV_FIRST.match(line)
        if first and _opens_call(lines, index):
            yield "bare_python", index + 1, first.group(1)


def _opens_call(lines, index) -> bool:
    """True when the nearest non-blank line above `index` ends in a `[` or a `(`."""

    for above in range(index - 1, -1, -1):
        if lines[above].strip():
            return lines[above].rstrip().endswith(("[", "("))
    return False


def _fenced_indexes(lines):
    inside = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            inside = not inside
        elif inside:
            yield index


def _shell_hits(lines, indexes):
    for index in indexes:
        if _is_comment(lines[index]):
            continue
        for match in _SHELL_COMMAND.finditer(lines[index]):
            yield "bare_python", index + 1, match.group(1)


def _ticket_python_hits(lines):
    for index, line in enumerate(lines):
        for match in _TICKET_COMMAND.finditer(line):
            yield "bare_python", index + 1, match.group(1)


def _os_name_hits(lines):
    for index, line in enumerate(lines):
        for match in _OS_CALL.finditer(line):
            yield "one_os_call", index + 1, match.group(0)


def _os_call_hits(lines):
    for index, line in enumerate(lines):
        if _is_comment(line) or not _OS_CALL.search(line) or _guarded(lines, index):
            continue
        for match in _OS_CALL.finditer(line):
            yield "one_os_call", index + 1, match.group(0)


def _guarded(lines, index) -> bool:
    """True when the OS-only call at `index` sits in a branch on the OS: an OS test in the same function (or a
    module-level one) within `GUARD_WINDOW` lines above, or an import inside `try` with an `except ImportError`."""

    if _in_import_error_block(lines, index):
        return True
    scope = _scope(lines, index)
    for above in range(max(0, index - GUARD_WINDOW), index):
        if _OS_TEST.match(lines[above]):
            guard_scope = _scope(lines, above)
            if guard_scope == scope or (_indent(lines[above]) == 0 and guard_scope == -1):
                return True
    return False


def _scope(lines, index) -> int:
    """The line of the nearest `def` above `index` with less indentation, or -1 at module level."""

    indent = _indent(lines[index])
    for above in range(index - 1, -1, -1):
        if _DEF.match(lines[above]) and _indent(lines[above]) < indent:
            return above
    return -1


def _in_import_error_block(lines, index) -> bool:
    if not _IMPORT.match(lines[index]):
        return False
    indent = _indent(lines[index])
    head = next((above for above in range(index - 1, -1, -1)
                 if lines[above].strip() and _indent(lines[above]) < indent), None)
    if head is None or not _TRY.match(lines[head]):
        return False
    head_indent = _indent(lines[head])
    for below in range(index + 1, len(lines)):
        if lines[below].strip() and _indent(lines[below]) <= head_indent:
            return _IMPORT_ERROR.match(lines[below]) is not None
    return False


def _name_literal_hits(lines, job_names):
    literal = _name_pattern(job_names, "\"'")
    if literal is None:
        return
    for index, line in enumerate(lines):
        if _is_comment(line):
            continue
        for match in literal.finditer(line):
            yield "check_name_literal", index + 1, match.group(2)


def _quoted_name_hits(lines, job_names):
    literal = _name_pattern(job_names, "\"'`")
    if literal is None:
        return
    for index, line in enumerate(lines):
        for match in literal.finditer(line):
            yield "check_name_literal", index + 1, match.group(2)


def _jobs_block(lines) -> list[str]:
    """The lines under the top-level `jobs:` key, blank and comment lines left out."""

    block, inside = [], False
    for line in lines:
        if not inside:
            inside = _JOBS_KEY.match(line) is not None
            continue
        if not line.strip() or _is_comment(line):
            continue
        if not line[0].isspace():  # the next top-level key ends `jobs:`
            break
        block.append(line)
    return block


def _job_names(lines) -> set[str]:
    """Each job's key and the names its `name:` field gives, read from the lines of one workflow file."""

    block = _jobs_block(lines)
    if not block:
        return set()
    job_indent = min(_indent(line) for line in block)
    names: set[str] = set()
    for index, line in enumerate(block):
        if _indent(line) != job_indent:
            continue
        key = _JOB_NAME.match(line.strip())
        if key is None:
            continue
        names.add(key.group(1))
        body = []
        for below in block[index + 1:]:
            if _indent(below) <= job_indent:
                break
            body.append(below)
        if body:
            field_indent = min(_indent(field) for field in body)
            for field in body:
                if _indent(field) == field_indent:
                    names |= _name_values(field.strip())
    return names


def _name_values(field: str) -> set[str]:
    """The names a `name:` field gives: its value, and the part before a matrix suffix ` (`."""

    match = _NAME_FIELD.match(field)
    if not match:
        return set()
    value = match.group(1).strip("'\"")
    names: set[str] = set()
    if value and "${{" not in value:
        names.add(value)
    bare = value.split(" (")[0]
    if bare and "${{" not in bare:
        names.add(bare)
    return names
