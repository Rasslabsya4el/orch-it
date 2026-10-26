"""What one tool call would delete, move or stop (#395 decisions 1, 2, 3): the trigger. It turns a
command into the list of actions it performs, each naming its object as spelled; the guard reads
the verdict from the object, never from these words. The aim is that no way of writing a delete,
move or stop slips past, and that a look-alike yields nothing.

A target the trigger cannot read — a variable, `$(...)`, a backtick, a computed value, a
name fed in from a pipe — is `None`, so the guard refuses rather than guess (decisions 2, 3)."""

from __future__ import annotations

import ntpath
import posixpath
import re
from dataclasses import dataclass, field

from orch_it.guards import shell

MAX_SEGMENT = 120

# the closed sets of the strings the actions carry (kept in step with `_match` by a test)
RULE_IDS = ("rm", "rmdir", "del", "unlink", "remove-item", "find-delete", "find-exec-rm",
            "rsync-delete", "robocopy-mir", "git-worktree-remove", "git-worktree-prune",
            "python-c", "node-e", "mv", "move", "move-item", "ren", "rename-item",
            "git-worktree-move", "kill", "pkill", "killall", "taskkill", "tskill",
            "stop-process", "wmic-terminate", "cim-terminate", "python-os-kill",
            "node-process-kill", "unreadable-command")
MATCHER_KINDS = ("name_glob", "name_regex", "cmdline_regex")


@dataclass(frozen=True)
class Delete:
    """A command that would delete. `targets`: each as spelled (None: unreadable); `cwd`: the
    directory it runs in (None: unreadable); `recursive`: the delete descends into folders;
    `walk_links`: it may follow links (False only for a `find` without `-L`); `roots_too`: the
    targets themselves are deleted (False only for a filtered `find`, which deletes only what
    matches below its start); `rule_id`: the trigger; `segment`: the simple command, ≤120."""

    targets: tuple[str | None, ...]
    cwd: str | None
    recursive: bool
    walk_links: bool
    roots_too: bool
    rule_id: str
    segment: str


@dataclass(frozen=True)
class Move:
    """A command that would move or rename. `sources`: what is moved away (never the
    destination); the rest as `Delete`."""

    sources: tuple[str | None, ...]
    cwd: str | None
    rule_id: str
    segment: str


@dataclass(frozen=True)
class Matcher:
    """A process target named by text, not pid. `kind`: `name_glob` (a name with `*`/`?`,
    as `taskkill /IM`, `Stop-Process -Name`, `killall`, `tskill`), `name_regex` (a `pkill`
    pattern on the name), `cmdline_regex` (`pkill -f` on the whole command line); `exact`:
    the whole name must equal (`killall -e`, `pkill -x`)."""

    kind: str
    text: str
    exact: bool = False


@dataclass(frozen=True)
class Stop:
    """A command that would stop a process. `pids`: the pids named; `matchers`: the targets
    named by text; `opaque`: some target could not be read; `tree`: the stop takes a whole
    tree or group (`taskkill /T`, a negative pid), so the guard judges every process of it;
    `rule_id`: the trigger; `segment`: ≤120."""

    pids: tuple[int, ...]
    matchers: tuple[Matcher, ...]
    opaque: bool
    tree: bool
    rule_id: str
    segment: str


Action = Delete | Move | Stop


def actions(command: str, *, tool: str, cwd: str, env, windows: bool) -> list[Action]:
    """Every delete, move and stop `command` would perform. `tool` is "Bash" (a POSIX shell,
    Git Bash included) or "PowerShell"; wrappers switch the dialect. Never raises on any text."""

    out: list[Action] = []
    for simple in shell.simple_commands(command, tool=tool, cwd=cwd, env=env, windows=windows):
        _read_tree(simple, windows, out, 0)
    return out


def _read_tree(simple: shell.Simple, windows: bool, out: list[Action], depth: int) -> None:
    _read_simple(simple, windows, out, depth)
    for sub in simple.subs:  # a command substitution's own commands run too, at any depth
        _read_tree(sub, windows, out, depth + 1)


# the shell keywords that lead a simple command without being the command (so a delete after
# them is not hidden): `for x; do rm`, `if c; then rm`, `! rm`, `{ rm; }`
_LEAD_KEYWORDS = {"do", "then", "else", "elif", "!", "{", "}", "time", "nocorrect"}
_SKIP_WHOLE = {"for", "while", "until", "if", "case", "fi", "done", "esac", "in", "select", "function"}


def _read_simple(simple: shell.Simple, windows: bool, out: list[Action], depth: int = 0) -> None:
    if simple.stdin is not None and simple.stdin_to_shell:
        # `cat <<EOF | bash`: the heredoc body is the script the shell runs
        _run_text(simple.stdin, "posix", windows, out, simple.cwd, depth)
    words = _arm_commands(list(simple.argv))
    if words and words[0] in _SKIP_WHOLE:
        return  # a control-flow header (`for x in ...`) runs no delete itself
    argv = _unwrap(words, simple.dialect, windows, out, simple.cwd, depth, simple.stdin)
    if not argv:  # None: the wrapper's work is done; []: nothing left to read
        return
    if argv[0] is None:
        out.append(_opaque_head(argv, simple))
        return
    _emit(_match(argv, simple, windows, depth), out)


_CASE_LABEL = re.compile(r"^\(?[^()\s]*\)(.*)$", re.S)


def _arm_commands(words: list) -> list:
    """The words of the command itself: shell keywords that lead it dropped, a `case ... in`
    header dropped (the arm's own words follow), and a case arm's label (`a)`, `(a)`, `*)`,
    `a)rm`) dropped, so each arm's command is read as the simple command it is. A header with no
    `in`, or a label with nothing after it, leaves nothing."""

    for _ in range(64):
        while words and words[0] in _LEAD_KEYWORDS:
            words = words[1:]
        if not words or words[0] is None:
            return words
        if words[0] == "case":
            if "in" not in words:
                return []
            words = words[words.index("in") + 1:]
            continue
        found = _CASE_LABEL.match(words[0])
        if found is None:
            return words
        words = ([found.group(1)] if found.group(1) else []) + words[1:]
    return []


def _emit(found, out: list[Action]) -> None:
    """Add what a matcher found: nothing, one action, or several."""

    if found is None:
        return
    if isinstance(found, list):
        out.extend(found)
    else:
        out.append(found)


def _opaque_head(argv, simple: shell.Simple) -> Delete:
    """A command whose head word cannot be read (`$(echo rm) -rf x`, `rm${IFS}-rf${IFS}x`, or
    nesting past `shell.MAX_DEPTH`) is one opaque delete over every argument that is not a pure
    option flag, so the guard refuses it only when one of them is a protected path. With no
    argument left the word itself may hold the path, so the target is unreadable (None)."""

    targets = tuple(w for w in argv[1:] if w is None or (w != "--" and not _is_flag(w)))
    return Delete(targets or (None,), simple.cwd, True, True, True, "unreadable-command",
                  _segment(argv))


def _is_flag(word: str) -> bool:
    return len(word) > 1 and word.startswith("-")


# --- wrappers --------------------------------------------------------------------------------

_POSIX_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_PASS_THROUGH = {"env", "sudo", "nice", "nohup", "setsid", "stdbuf", "ionice", "doas", "command",
                 "builtin", "exec", "time"}


_EVAL = {"eval", "iex", "invoke-expression"}


def _unwrap(argv, dialect, windows, out, cwd, depth, stdin=None):
    """Peel wrapper commands (`bash -c '...'`, `eval`, `env`, `sudo`, `timeout`, `xargs`,
    `cmd /c`, `pwsh -Command`, `wsl`), running an inner shell string back through the splitter.
    Returns the real argv to match, or None when the wrapper's work is already handled (an
    inner string parsed) or nothing is left to read."""

    guard = 0
    while argv and argv[0] is not None and guard < 32:
        guard += 1
        name = _name(argv[0], windows or dialect == "windows")
        rest = argv[1:]
        if name in _PASS_THROUGH:
            argv = _after_assignments(rest) if name == "env" else _skip_leading_options(rest)
            continue
        if name == "timeout":
            argv = _after_timeout(rest)
            continue
        if name in _EVAL:
            return _run_inner_string(rest, dialect, windows, out, cwd, depth)
        if name in _XARGS_NAMES:
            inner = _xargs_inner(rest)
            if inner is None:
                return None
            # xargs feeds its targets from input the guard cannot read, so the inner command
            # acts on an unreadable target: a trailing None makes the delete or stop opaque
            argv = inner + [None]
            continue
        if dialect == "posix" and name in _POSIX_SHELLS:
            if stdin is not None and _shell_reads_stdin(rest):
                return _run_text(stdin, "posix", windows, out, cwd, depth)  # `bash <<EOF`
            return _run_inner_string(rest, "posix", windows, out, cwd, depth)
        if (windows or dialect == "windows") and name in ("sh", "bash") and _has_c(rest):
            return _run_inner_string(rest, "posix", windows, out, cwd, depth)
        if name == "cmd":
            return _dispatch_inner(_after_cmd(rest), "windows", windows, out, cwd, depth)
        if name in ("pwsh", "powershell"):
            return _run_inner_string(_after_pwsh(rest), "windows", windows, out, cwd, depth)
        if name == "wsl":
            return _dispatch_inner(_after_wsl(rest), "posix", False, out, cwd, depth)
        return argv
    return argv


_OPTION_VALUES = {"-n", "--adjustment", "-u", "--user", "-g", "--group", "-c", "--class",
                  "-p", "--priority"}


def _skip_leading_options(rest):
    """Drop a wrapper's own leading options (and the value of one that takes a value, as
    `nice -n 5`) to reach the real command."""

    i = 0
    while i < len(rest) and rest[i] is not None and rest[i].startswith("-") and rest[i] != "--":
        if rest[i] in _OPTION_VALUES:
            i += 2
        else:
            i += 1
    if i < len(rest) and rest[i] == "--":
        i += 1
    return rest[i:]


def _dispatch_inner(words, dialect, windows, out, cwd, depth):
    """Re-read an unwrapped argv (`cmd /c rmdir ...`, `wsl rm ...`): a single word holding a
    whole command line is parsed as a string, otherwise the words are one simple command."""

    readable = [w for w in words if w is not None]
    if len(words) == 1 and words[0] is not None and any(c in words[0] for c in " \t;&|"):
        return _run_inner_string(words, dialect, windows, out, cwd, depth)
    if not readable:
        return None
    _read_simple(shell.Simple(list(words), cwd, "windows" if dialect == "windows" else "posix"),
                 windows, out, depth + 1)
    return None


_XARGS_NAMES = {"xargs"}


def _run_inner_string(rest, dialect, windows, out, cwd, depth):
    """Parse the `-c`/`-Command` string (the first non-option word) as its own command list."""

    script = _first_non_option(rest)
    if script is None:
        return None
    return _run_text(script, dialect, windows, out, cwd, depth)


def _run_text(script, dialect, windows, out, cwd, depth):
    """Parse `script` as a command list of `dialect` and read every command in it. Always None:
    the caller's own work is done."""

    tool = "PowerShell" if dialect == "windows" else "Bash"
    for simple in shell.simple_commands(script, tool=tool, cwd=cwd, env={}, windows=windows,
                                        depth=depth + 1):
        _read_tree(simple, windows, out, depth + 1)
    return None


def _shell_reads_stdin(rest) -> bool:
    """A shell with no script file and no `-c` (`bash`, `bash -s`, `sh -`, `bash -e`) reads its
    commands from stdin."""

    i = 0
    while i < len(rest):
        word = rest[i]
        if word is None:
            return False
        if word in ("-s", "-"):
            return True
        if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
            return False  # `-c`, `-ec`: the command string is its own argument
        if word in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 2
            continue
        if word.startswith(("-", "+")):
            i += 1
            continue
        return False  # a script file: stdin is its data
    return True


def _name(word: str, windows: bool) -> str:
    base = ntpath.basename(word).lower() if windows else posixpath.basename(word)
    if windows:
        for suffix in (".exe", ".cmd", ".bat", ".ps1", ".com"):
            if base.endswith(suffix):
                return base[: -len(suffix)]
    elif base.endswith(".exe"):
        base = base[:-4]
    return base


def _after_assignments(rest):
    """Past `env`'s assignments and its own options (`-i`, `-u NAME`, `-C dir`, `-S ...`) to
    the command it runs."""

    i = 0
    while i < len(rest) and rest[i] is not None and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[i]):
        i += 1
    while i < len(rest) and rest[i] is not None and rest[i].startswith("-") and rest[i] != "--":
        if rest[i] in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string"):
            i += 2
        else:
            i += 1
    if i < len(rest) and rest[i] == "--":
        i += 1
    return rest[i:]


def _after_timeout(rest):
    i = 0
    while i < len(rest) and rest[i] is not None and rest[i].startswith("-"):
        i += 1
    if i < len(rest):  # the duration
        i += 1
    while i < len(rest) and rest[i] is not None and rest[i].startswith("-"):
        i += 1
    return rest[i:]


def _xargs_inner(rest):
    i = 0
    while i < len(rest) and rest[i] is not None and rest[i].startswith("-"):
        # -I{}, -n N, -P N take a value
        if rest[i] in ("-n", "-P", "-I", "-L", "-s", "-d", "--max-args", "--max-procs"):
            i += 2
        else:
            i += 1
    tail = rest[i:]
    return tail or [None]


def _has_c(rest):
    return any(w == "-c" for w in rest if w is not None)


_CMD_FLAGS = {"/c", "/k", "/s", "/q", "/d", "/v:on", "/v:off", "/a", "/u", "/e:on", "/e:off"}


def _after_cmd(rest):
    i = 0
    # Git Bash doubles a leading slash (`cmd //c`), so read `//c` as `/c`
    while i < len(rest) and rest[i] is not None and _deslash(rest[i]).lower() in _CMD_FLAGS:
        i += 1
    return rest[i:]


def _deslash(word: str) -> str:
    return "/" + word[2:] if word.startswith("//") else word


def _after_pwsh(rest):
    i = 0
    while i < len(rest):
        word = rest[i]
        if word is None:
            return rest[i:]
        low = word.lower()
        if low in ("-command", "-c", "-encodedcommand", "-ec"):
            return rest[i + 1:]
        if low.startswith("-"):
            i += 1
            if low in ("-executionpolicy", "-ep", "-file", "-configurationname", "-outputformat"):
                i += 1
            continue
        return rest[i:]
    return []


def _after_wsl(rest):
    i = 0
    while i < len(rest) and rest[i] is not None and rest[i].startswith("-"):
        if rest[i] in ("-d", "--distribution", "-u", "--user", "--cd"):
            i += 2
        elif rest[i] in ("-e", "--exec", "--"):
            i += 1
            break
        else:
            i += 1
    return rest[i:]


def _first_non_option(rest):
    skip_next = False
    for i, word in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if word is None:
            return None
        if word.startswith("-") and len(word) > 1 and not word[1].isdigit():
            if word in ("-c", "-Command", "-command"):
                return rest[i + 1] if i + 1 < len(rest) else None
            continue
        return word
    return None


# --- the trigger words -----------------------------------------------------------------------

_RM = {"rm", "ri", "remove-item", "unlink"}
_RMDIR = {"rmdir", "rd"}
_DEL = {"del", "erase"}
_MOVE = {"mv", "move", "move-item", "ren", "rename", "rename-item"}
_RECURSIVE_FLAGS_POSIX = {"-r", "-R", "--recursive", "-rf", "-fr", "-Rf", "-fR"}


def _match(words: list[str | None], simple: shell.Simple, windows: bool, depth: int = 0):
    """The action(s) one simple command performs: None, one action, or a list."""

    is_win = windows or simple.dialect == "windows"
    head = _name(words[0], is_win)
    segment = _segment(words)
    if _asks_help(head, words):
        return None
    if head in _RM:
        return _delete(words, simple, segment, rule=_rm_rule(head), recursive=_rm_recursive(head, words))
    if head in _RMDIR:
        return _delete(words, simple, segment, rule="rmdir", recursive=True)
    if head in _DEL:
        return _delete(words, simple, segment, rule="del", recursive=_has_flag(words, {"/s"}, is_win))
    if head in _MOVE:
        return _move(words, simple, segment, head, is_win)
    if head == "find":
        return _find(words, simple, segment)
    if head in ("robocopy",):
        return _robocopy(words, simple, segment)
    if head in ("rsync",):
        return _rsync(words, simple, segment)
    if head == "git":
        return _git(words, simple, segment, is_win)
    if head in _STOP_HEADS:
        return _stop(head, words, simple, segment, is_win)
    if re.fullmatch(r"python[0-9.]*|py|pypy[0-9.]*", head):
        return _python_c(words, simple, segment, windows, depth)
    if head in ("node", "nodejs", "deno", "bun"):
        return _node_e(head, words, simple, segment, windows, depth)
    return None


def _asks_help(head: str, words) -> bool:
    """`rm --help`, `taskkill /?`: the command prints its usage and acts on nothing. `-h` counts
    only with no target beside it (it is `human-readable` to `rsync`). An interpreter's flags are
    not read: its body is code."""

    if head in _INTERPRETERS or re.fullmatch(r"python[0-9.]*|py|pypy[0-9.]*", head):
        return False
    rest = words[1:]
    if any(w in ("--help", "/?", "-?") for w in rest):
        return True
    return "-h" in rest and all(w is not None and (w == "--" or _is_flag(w))
                                 for w in rest)


_INTERPRETERS = {"node", "nodejs", "deno", "bun", "perl", "ruby", "pwsh", "powershell"}


def _rm_rule(head: str) -> str:
    return {"remove-item": "remove-item", "ri": "remove-item", "unlink": "unlink"}.get(head, "rm")


def _rm_recursive(head: str, words) -> bool:
    if head == "unlink":
        return False
    return any(w in _RECURSIVE_FLAGS_POSIX or (w or "").lower() in ("-recurse", "--recurse")
               or _bundled_r(w) for w in words[1:])


def _bundled_r(word: str | None) -> bool:
    return bool(word) and word.startswith("-") and not word.startswith("--") and (
        "r" in word.lower()) and all(c.isalpha() or c == "-" for c in word)


def _delete(words, simple, segment, *, rule, recursive, walk_links=True, roots_too=True):
    targets = tuple(_targets(words[1:], simple.dialect))
    if not targets:
        targets = (None,)  # a recognised delete with no readable target: unreadable, so refused
    return Delete(targets, simple.cwd, recursive, walk_links, roots_too, rule, segment)


_PS_PATH_SWITCH = re.compile(r"-(?:Literal)?Path:(.+)$", re.IGNORECASE)


def _targets(rest, dialect):
    """The delete/move targets among `rest`: switches and redirections dropped, a PowerShell
    `-Path:X` switch read for its value, a PowerShell comma list split, a None word kept as an
    unreadable target."""

    out: list[str | None] = []
    for word in rest:
        if word is None:
            out.append(None)
        elif word == "--":
            continue
        elif dialect == "windows":
            if word.startswith(("-", "/")):
                found = _PS_PATH_SWITCH.match(word)
                if found:
                    out += _split_commas(found.group(1))
                continue
            out += _split_commas(word)
        elif word.startswith("-") and word != "-":
            continue
        else:
            out.append(word)
    return out


def _split_commas(value: str) -> list[str]:
    """A PowerShell array literal (`a,b,c`) split into its items; a lone value stays whole."""

    return [piece for piece in value.split(",") if piece] if "," in value else [value]


_MOVE_DEST_SWITCH = ("-destination", "-t", "--target-directory")


def _move(words, simple, segment, head, is_win):
    rest = words[1:]
    moved: list[str | None] = []
    i = 0
    take_source_next = False
    while i < len(rest):
        word = rest[i]
        low = (word or "").lower()
        if word is not None and (low in ("-destination", "-t") or low == "--target-directory"):
            i += 2  # the destination and its value: never a source
            continue
        if word is not None and low.startswith("--target-directory="):
            i += 1
            continue
        if word is not None and (low in ("-path", "-literalpath")):
            take_source_next = True
            i += 1
            continue
        for target in _targets([word], simple.dialect):
            moved.append(target)
        take_source_next = False
        i += 1
    # a plain `mv A B C DIR` moves everything but the last word; a `-t`/`-Path` form took all
    if not any((w or "").lower() in _MOVE_DEST_SWITCH or (w or "").lower() in ("-path", "-literalpath")
               for w in rest) and len(moved) >= 2:
        moved = moved[:-1]
    if not moved:
        moved = [None]
    rule = {"mv": "mv", "move": "move", "move-item": "move-item", "ren": "ren",
            "rename": "ren", "rename-item": "rename-item"}[head]
    return Move(tuple(moved), simple.cwd, rule, segment)


def _find(words, simple, segment):
    rest = words[1:]
    if not any(w in ("-delete", "-exec", "-execdir", "-ok", "-okdir") or
               (w or "").startswith("-delete") for w in rest):
        return None
    deletes_rm = any(w in ("-exec", "-execdir", "-ok", "-okdir") for w in rest) and any(
        _name(w, False) in {"rm", "unlink"} for w in rest if w)
    if "-delete" not in rest and not deletes_rm:
        return None
    filtered = any(w in _FIND_FILTERS for w in rest)
    walk_links = any(w in ("-L", "-follow") for w in rest)
    starts = []
    for word in rest:
        if word is None:
            starts.append(None)
            break
        if word in ("-L", "-H", "-P"):
            continue  # find's own options come before the start paths
        if word.startswith("-") or word in ("(", ")", "!"):
            break
        starts.append(word)
    rule = "find-delete" if "-delete" in rest else "find-exec-rm"
    return Delete(tuple(starts) or (simple.cwd,), simple.cwd, True, walk_links,
                  roots_too=not filtered, rule_id=rule, segment=segment)


_FIND_FILTERS = {"-name", "-iname", "-path", "-ipath", "-regex", "-iregex", "-type", "-newer",
                 "-mtime", "-mmin", "-ctime", "-size", "-empty", "-user", "-group", "-perm",
                 "-wholename", "-lname", "-ilname"}


def _robocopy(words, simple, segment):
    if not _has_flag(words, {"/mir", "/purge"}, True):
        return None
    reals = [w for w in words[1:] if w is None or not w.startswith("/")]
    dest = reals[1] if len(reals) >= 2 else None
    return Delete((dest,), simple.cwd, True, True, True, "robocopy-mir", segment)


def _rsync(words, simple, segment):
    if not any((w or "").startswith("--delete") or w == "--del" for w in words[1:]):
        return None
    reals = [w for w in words[1:] if w is None or not w.startswith("-")]
    dest = reals[-1] if reals else None
    return Delete((dest,), simple.cwd, True, True, True, "rsync-delete", segment)


_GIT_OPT_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def _git(words, simple, segment, is_win):
    rest = _after_git_options(words[1:])
    sub = rest[0] if rest and rest[0] and not rest[0].startswith("-") else None
    if sub != "worktree":
        return None
    after = rest[1:]
    verb = next((w for w in after if w and not w.startswith("-")), None)
    if verb == "remove":
        targets = _targets([w for w in after if w != "remove"], simple.dialect)
        return Delete(tuple(targets) or (None,), simple.cwd, True, True, True,
                      "git-worktree-remove", segment)
    if verb == "move":
        tail = [w for w in after if w != "move"]
        srcs = _targets(tail, simple.dialect)
        moved = srcs[:1] if srcs else [None]
        return Move(tuple(moved), simple.cwd, "git-worktree-move", segment)
    if verb == "prune":
        return Delete((simple.cwd,), simple.cwd, True, True, True, "git-worktree-prune", segment)
    return None


def _after_git_options(rest):
    """Past git's own options before the subcommand (`-C dir`, `-c k=v`, `--git-dir=...`), which
    otherwise hide `worktree`."""

    i = 0
    while i < len(rest) and rest[i] is not None and rest[i].startswith("-"):
        if rest[i] in _GIT_OPT_WITH_VALUE:
            i += 2
        else:
            i += 1
    return rest[i:]


def _has_flag(words, flags, is_win) -> bool:
    low = {f.lower() for f in flags}
    return any(_deslash(w or "").lower() in low for w in words[1:])


# --- stop ------------------------------------------------------------------------------------

_STOP_HEADS = {"kill", "pkill", "killall", "taskkill", "tskill", "stop-process", "spps",
               "wmic", "invoke-cimmethod"}


def _stop(head, words, simple, segment, is_win):
    rest = words[1:]
    if head == "kill":
        return _kill(rest, simple, segment)
    if head in ("pkill",):
        return _pkill(rest, simple, segment)
    if head in ("killall",):
        return _killall(rest, simple, segment)
    if head in ("taskkill",):
        return _taskkill(rest, simple, segment)
    if head in ("tskill",):
        return _tskill(rest, simple, segment)
    if head in ("stop-process", "spps"):
        return _stop_process(rest, simple, segment)
    if head in ("wmic",):
        return _wmic(rest, simple, segment)
    if head in ("invoke-cimmethod",):
        return _cim(rest, simple, segment)
    return None


def _finish_stop(pids, matchers, opaque, tree, rule, segment) -> Stop:
    """A recognised stop that resolves to no pid and no matcher is opaque: the guard must not
    read it as stopping nothing (a pipeline-fed stop, an empty `taskkill`)."""

    if not pids and not matchers:
        opaque = True
    return Stop(tuple(pids), tuple(matchers), opaque, tree, rule, segment)


def _ints(values):
    pids, opaque = [], False
    for value in values:
        if value is None:
            opaque = True
        elif re.fullmatch(r"-?\d+", value):
            pids.append(int(value))
        else:
            opaque = True
    return pids, opaque


def _kill(rest, simple, segment):
    """`kill [-SIG|-s SIG] <pid|-pgid|%job>...`: every `-...` is a signal or option, not a pid;
    `--` ends the options; a bare number is a pid, a negative one a process group (a tree), a
    job spec or name is unreadable."""

    pids, opaque, tree = [], False, False
    if _kill_lists_or_probes(rest):
        return None  # `kill -l` lists signals; signal 0 only tests that a pid exists
    rest = _after_kill_signal(rest)  # the leading signal (`-9`, `-s KILL`, `-SIGTERM`) is no pid
    for word in rest:
        if word is None:
            opaque = True
        elif word == "--":
            continue
        elif re.fullmatch(r"-\d+", word):
            tree = True  # a negative pid is a whole process group
            value = -int(word)
            if value in (0, 1):
                opaque = True  # group 0/1: too broad to name
            else:
                pids.append(value)
        elif re.fullmatch(r"\d+", word):
            pids.append(int(word))
        elif word.startswith("-"):
            continue  # a stray option
        else:
            opaque = True  # a job spec %1, a name, $(...)
    return _finish_stop(pids, [], opaque, tree, "kill", segment)


def _kill_lists_or_probes(rest) -> bool:
    if not rest or rest[0] is None:
        return False
    first = rest[0]
    if first in ("-l", "-L", "--list", "--table"):
        return True
    if first == "-0" or first in ("--signal=0", "-s0", "-n0"):
        return True
    return first in ("-s", "--signal", "-n") and len(rest) > 1 and rest[1] == "0"


def _after_kill_signal(rest):
    """Drop `kill`'s leading signal argument (`-9`, `-SIGKILL`, `-s KILL`, `-n 9`) so it is not
    read as a pid; the rest are pids, process groups or an unreadable target."""

    if not rest or rest[0] is None:
        return rest
    first = rest[0]
    if first in ("-s", "--signal", "-q", "-n"):
        return rest[2:]
    if first.startswith("-") and first != "--":
        return rest[1:]
    if first == "--":
        return rest[1:]
    return rest


def _pkill(rest, simple, segment):
    matchers, opaque, exact, full, tree = [], False, False, False, False
    names = []
    i = 0
    while i < len(rest):
        word = rest[i]
        if word is None:
            opaque = True
        elif word in ("--full",):
            full = True
        elif word in ("--exact",):
            exact = True
        elif word in ("-P", "--parent", "-s", "--session", "-g", "--pgroup", "-u", "-U", "-G", "-t",
                      "--signal", "-n", "-o"):
            opaque = True  # selects by parent/user/session/newest, not by name: more than we read
            i += 2
            continue
        elif re.fullmatch(r"-[a-zA-Z0-9]+", word):
            # bundled short flags, `-9f` / `-fx`: read -f (full) and -x (exact), ignore a signal
            letters = word[1:]
            if "f" in letters:
                full = True
            if "x" in letters:
                exact = True
        elif word.startswith("--signal="):
            pass
        elif word.startswith("-"):
            pass
        else:
            names.append(word)
        i += 1
    for name in names:
        matchers.append(Matcher("cmdline_regex" if full else "name_regex", name, exact))
    return _finish_stop([], matchers, opaque, tree, "pkill", segment)


def _killall(rest, simple, segment):
    matchers, opaque, exact = [], False, False
    for word in rest:
        if word is None:
            opaque = True
        elif word in ("-e", "--exact"):
            exact = True
        elif word.startswith("-"):
            if word in ("-s", "--signal", "-u", "--user", "-g"):
                opaque = True
        else:
            matchers.append(Matcher("name_glob", word, exact))
    return _finish_stop([], matchers, opaque, False, "killall", segment)


def _taskkill(rest, simple, segment):
    pids, matchers, opaque, tree = [], [], False, False
    i = 0
    while i < len(rest):
        word = _deslash(rest[i] or "")  # Git Bash doubles a leading slash: `//F`, `//PID`
        low = word.lower()
        if rest[i] is None:
            opaque = True
        elif low == "/t":
            tree = True
        elif low == "/pid":
            i += 1
            value = rest[i] if i < len(rest) else None
            if value is not None and re.fullmatch(r"\d+", value):
                pids.append(int(value))
            else:
                opaque = True
        elif low == "/im":
            i += 1
            value = rest[i] if i < len(rest) else None
            if value is None:
                opaque = True
            else:
                matchers.append(Matcher("name_glob", value))
        elif low in ("/fi", "/s", "/u", "/p", "/fo"):
            i += 1
            opaque = True  # a filter, or a remote machine: more than this command can read
        i += 1
    return _finish_stop(pids, matchers, opaque, tree, "taskkill", segment)


def _tskill(rest, simple, segment):
    pids, matchers, opaque = [], [], False
    for word in rest:
        if word is None:
            opaque = True
        elif word.startswith("/"):
            continue
        elif re.fullmatch(r"\d+", word):
            pids.append(int(word))
        else:
            matchers.append(Matcher("name_glob", word))
    return _finish_stop(pids, matchers, opaque, False, "tskill", segment)


def _stop_process(rest, simple, segment):
    pids, matchers, opaque = [], [], False
    i = 0
    while i < len(rest):
        word = rest[i]
        low = (word or "").lower()
        if word is None:
            opaque = True
        elif low in ("-id", "-i"):
            i += 1
            pids_part, op = _ints(_csv(rest[i] if i < len(rest) else None))
            pids += pids_part
            opaque = opaque or op
        elif low in ("-name", "-n"):
            i += 1
            value = rest[i] if i < len(rest) else None
            if value is None:
                opaque = True
            else:
                for piece in _csv(value):
                    matchers.append(Matcher("name_glob", piece))
        elif low in ("-inputobject", "-force", "-passthru", "-whatif", "-confirm"):
            if low == "-inputobject":
                opaque = True
                i += 1
        elif low.startswith("-"):
            pass
        elif re.fullmatch(r"\d+", word):
            pids.append(int(word))
        else:
            opaque = True
        i += 1
    return _finish_stop(pids, matchers, opaque, False, "stop-process", segment)


def _csv(value):
    if value is None:
        return [None]
    return [piece.strip() for piece in value.split(",") if piece.strip()]


def _wmic(rest, simple, segment):
    low = [(w or "").lower() for w in rest]
    if "process" not in low or not any(v in low for v in ("delete", "terminate", "call")):
        return None
    pids, matchers, opaque = [], [], True  # a WMIC where clause is more than this reads
    for word in rest:
        found = re.search(r"processid=(\d+)", (word or ""), re.IGNORECASE)
        if found:
            pids.append(int(found.group(1)))
    return Stop(tuple(pids), tuple(matchers), opaque, True, "wmic-terminate", segment)


def _cim(rest, simple, segment):
    low = " ".join((w or "").lower() for w in rest)
    if "terminate" not in low and "win32_process" not in low:
        return None
    return Stop((), (), True, True, "cim-terminate", segment)


# --- python -c / node -e ---------------------------------------------------------------------

# a delete call in a `-c`/`-e` body, with its first argument captured when it is a string
# literal; `_ARG` is the optional quoted first argument
_ARG = r"""\s*\(\s*(?:(?P<q>['"])(?P<lit>(?:\\.|[^'"\\])*)(?P=q))?"""
_PY_DELETE = re.compile(
    r"(?:shutil\.rmtree|os\.removedirs|os\.remove|os\.unlink|os\.rmdir"
    r"|(?<![.\w])rmtree|\.unlink|\.rmdir)" + _ARG)
_PY_STOP = re.compile(r"(?:os\.killpg|os\.kill|\.terminate|\.kill)\s*\(\s*(?:(\d+))?")
_NODE_DELETE = re.compile(
    r"(?:rmSync|rmdirSync|unlinkSync|\.rmSync|\.rm|\.rmdir|\.unlink)" + _ARG)
_NODE_STOP = re.compile(r"(?:process\.kill|\.kill|treeKill)\s*\(\s*(?:(\d+))?")


def _python_c(words, simple, segment, windows, depth):
    body = _dash_body(words, ("-c",))
    if body is None and "-c" not in words:
        body = _stdin_code(words[1:], simple, {"-W", "-X", "-Q"}, {"-c", "-m"})
    return _code_body(body, simple, segment, [(_PY_DELETE, "python-c")], _PY_STOP,
                      "python-os-kill", "python", windows, depth)


def _node_e(head, words, simple, segment, windows, depth):
    body = _dash_body(words, ("-e", "--eval", "-p", "--print"))
    if body is None and head == "deno" and len(words) > 1 and words[1] == "eval":
        body = _first_non_option(words[2:])
    if body is None and not any(w in ("-e", "--eval", "-p", "--print") for w in words):
        rest = words[1:]
        if head in ("deno", "bun") and rest[:1] == ["run"]:
            rest = rest[1:]
        body = _stdin_code(rest, simple, {"-r", "--require", "--loader", "--import",
                                          "--input-type"}, {"-c", "--check"})
    return _code_body(body, simple, segment, [(_NODE_DELETE, "node-e")], _NODE_STOP,
                      "node-process-kill", "node", windows, depth)


def _stdin_code(rest, simple, value_options, code_flags):
    """The heredoc body an interpreter runs when it is given no script file and no code string of
    its own (`python -`, `python3 -u`, `node -`, `deno run -`): its stdin is the program."""

    if simple.stdin is None:
        return None
    i = 0
    while i < len(rest):
        word = rest[i]
        if word is None or word in code_flags:
            return None
        if word == "-":
            return simple.stdin
        if word in value_options:
            i += 2
            continue
        if word.startswith("-"):
            i += 1
            continue
        return None  # a script file: the heredoc is its data
    return simple.stdin


def _dash_body(words, flags):
    rest = words[1:]
    for i, word in enumerate(rest):
        if word in flags:
            return rest[i + 1] if i + 1 < len(rest) else ""
        if word is None:
            return None  # `-c "$X"`: the script cannot be read, so no trigger is seen
    return None


def _code_body(body, simple, segment, delete_res, stop_re, stop_rule, flavor, windows, depth):
    """The deletes, stops and shell commands inside a `-c`/`-e` body. Shapes are matched on the
    code with its string and comment contents blanked, so a delete word written inside a string
    or comment does not fire; a delete call's own string-literal argument is the target (a
    non-literal argument is unreadable, so None). A shell command run through `os.system`,
    `subprocess` or `child_process` is parsed as the command it is."""

    if body is None:
        return None
    code = _blank_code(body, ("//", "/*") if flavor == "node" else ("#",))
    found: list[Action] = []
    for delete_re, rule in delete_res:
        deletes = list(delete_re.finditer(code))
        if deletes:
            targets = tuple(_arg_target(body, m) for m in deletes)
            found.append(Delete(targets, simple.cwd, True, True, True, rule, segment))
    stops = list(stop_re.finditer(code))
    if stops:
        pids = tuple(int(m.group(1)) for m in stops if m.group(1))
        opaque = any(not m.group(1) for m in stops)
        found.append(_finish_stop(list(pids), [], opaque, True, stop_rule, segment))
    found += _shell_calls(body, code, flavor, simple, segment, windows, depth)
    return found or None


# a call that runs a shell command (python: os.system/popen, subprocess; node: child_process)
_PY_SHELL_CALL = re.compile(
    r"(?<![\w.])(?:os\.(?P<os>system|popen)"
    r"|subprocess\.(?P<sp>run|call|Popen|check_call|check_output|getoutput|getstatusoutput))\s*\(")
_NODE_SHELL_CALL = re.compile(
    r"(?:(?<![\w$.])|(?:child_process|childProcess|cp)\s*\.\s*|(?<=\))\s*\.\s*)"
    r"(?P<fn>execSync|exec|spawnSync|spawn|execFileSync|execFile)\s*\(")


def _shell_calls(body, code, flavor, simple, segment, windows, depth) -> list[Action]:
    rule = "python-c" if flavor == "python" else "node-e"
    opaque = Delete((None,), simple.cwd, True, True, True, rule, segment)
    found: list[Action] = []
    pattern = _PY_SHELL_CALL if flavor == "python" else _NODE_SHELL_CALL
    for m in pattern.finditer(code):
        args, _ = _split_call(body, m.end() - 1)
        first = args[0] if args else ""
        items = _list_items(first)
        text = _literal(first)
        if flavor == "python":
            as_shell = bool(m.group("os")) or m.group("sp") in ("getoutput", "getstatusoutput") or any(
                re.fullmatch(r"shell\s*=\s*True", a) for a in args[1:])
            if items is not None and not m.group("os"):
                found += _run_argv(items, simple, windows, depth)
            elif as_shell and text is not None:
                found += _run_shell_text(text, simple, windows, depth)
            elif as_shell:
                found.append(opaque)
        elif m.group("fn") in ("exec", "execSync"):
            if text is not None:
                found += _run_shell_text(text, simple, windows, depth)
            else:
                found.append(opaque)
        elif items is not None:
            found += _run_argv(items, simple, windows, depth)
        elif text is not None:
            more = _list_items(args[1]) if len(args) > 1 else None
            if more is not None:
                found += _run_argv([text] + more, simple, windows, depth)
            elif re.search(r"\s", text):
                found += _run_shell_text(text, simple, windows, depth)
            else:
                found += _run_argv([text], simple, windows, depth)
    return found


def _run_shell_text(text, simple, windows, depth) -> list[Action]:
    local: list[Action] = []
    _run_text(text, "posix", windows, local, simple.cwd, depth)
    return local


def _run_argv(items, simple, windows, depth) -> list[Action]:
    local: list[Action] = []
    _read_tree(shell.Simple(list(items), simple.cwd, "posix"), windows, local, depth + 1)
    return local


def _string_end(text: str, i: int) -> int:
    """Where the string literal opened at `text[i]` ends (a triple-quoted one included)."""

    n = len(text)
    quote = text[i]
    if quote != "`" and text[i:i + 3] == quote * 3:
        close = text.find(quote * 3, i + 3)
        return n if close == -1 else close + 3
    j = i + 1
    while j < n:
        if text[j] == "\\":
            j += 2
        elif text[j] == quote:
            return j + 1
        else:
            j += 1
    return n


def _split_call(text: str, start: int) -> tuple[list[str], int]:
    """The top-level arguments (source text) of the call or list whose opener is at `text[start]`,
    and the index of its closer (the end of the text when unclosed)."""

    args: list[str] = []
    buf: list[str] = []
    depth = 0
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            end = _string_end(text, i)
            buf.append(text[i:end])
            i = end
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if "".join(buf).strip():
        args.append("".join(buf).strip())
    return args, i


_STRING_OPEN = re.compile("^(?P<p>[rRbBuUfF]{0,2})(?P<q>'''|\"\"\"|'|\"|`)")


def _literal(arg: str) -> str | None:
    """The text of a plain string literal `arg`, or None when it is anything else (a name, a
    sum, two literals side by side). A part computed inside an f-string or template literal
    becomes `${__x}`, which the shell parser reads as unreadable."""

    arg = arg.strip()
    opened = _STRING_OPEN.match(arg)
    if not opened:
        return None
    quote = opened.group("q")
    if _string_end(arg, opened.end("p")) != len(arg) or len(arg) < opened.end() + len(quote):
        return None
    text = arg[opened.end():len(arg) - len(quote)]
    prefix = opened.group("p").lower()
    if "r" not in prefix:
        text = re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), text, flags=re.S)
    if "f" in prefix:
        text = re.sub(r"(?<!\{)\{[^{}]*\}(?!\})", "${__x}", text).replace("{{", "{").replace("}}", "}")
    if quote == "`":
        text = re.sub(r"\$\{[^}]*\}", "${__x}", text)
    return text


def _list_items(arg: str) -> list[str | None] | None:
    """The items of a list literal `[...]` / `(...)` as string literals (None for any other item),
    or None when `arg` is not a list literal."""

    arg = arg.strip()
    if arg[:1] not in ("[", "("):
        return None
    items, end = _split_call(arg, 0)
    if end != len(arg) - 1:
        return None
    return [_literal(item) for item in items]


def _arg_target(body: str, match: re.Match) -> str | None:
    """The literal path a delete call acts on, read from the original body at the call's
    position; None when its argument is not a plain string literal (a variable, a join)."""

    tail = body[match.start():]
    found = re.match(r"[^(]*\(\s*(['\"])((?:\\.|[^'\"\\])*)\1\s*[),]", tail)
    return found.group(2) if found else None


def _blank_code(body: str, comments=("#",)) -> str:
    """`body` with the contents of every string literal and every comment replaced by spaces, so
    only real code is left to match shapes against; every character keeps its position."""

    out = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch in "'\"`":
            end = _string_end(body, i)
            size = end - i
            out.append(ch + " " * (size - 2) + ch if size >= 2 and body[end - 1] == ch
                       else ch + " " * (size - 1))
            i = end
        elif "/*" in comments and body[i:i + 2] == "/*":
            end = body.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append(" " * (end - i))
            i = end
        elif any(body.startswith(c, i) for c in comments if c != "/*"):
            while i < n and body[i] != "\n":
                out.append(" ")
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _segment(words) -> str:
    text = " ".join("?" if w is None else w for w in words)
    return text[:MAX_SEGMENT]
