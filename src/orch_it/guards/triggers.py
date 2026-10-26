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
            "node-process-kill")
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
    words = list(simple.argv)
    while words and words[0] in _LEAD_KEYWORDS:
        words = words[1:]
    if words and words[0] in _SKIP_WHOLE:
        return  # a control-flow header (`for x in ...`) runs no delete itself
    argv = _unwrap(words, simple.dialect, windows, out, simple.cwd, depth)
    if argv is None:
        return
    if not argv or argv[0] is None:
        return
    action = _match(argv, simple, windows)
    if action is not None:
        out.append(action)


# --- wrappers --------------------------------------------------------------------------------

_POSIX_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_PASS_THROUGH = {"env", "sudo", "nice", "nohup", "setsid", "stdbuf", "ionice", "doas", "command",
                 "builtin", "exec", "time"}


_EVAL = {"eval", "iex", "invoke-expression"}


def _unwrap(argv, dialect, windows, out, cwd, depth):
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
    tool = "PowerShell" if dialect == "windows" else "Bash"
    for simple in shell.simple_commands(script, tool=tool, cwd=cwd, env={}, windows=windows,
                                        depth=depth + 1):
        _read_tree(simple, windows, out, depth + 1)
    return None


def _name(word: str, windows: bool) -> str:
    base = (ntpath.basename(word) if windows else posixpath.basename(word)).lower()
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


def _match(words: list[str | None], simple: shell.Simple, windows: bool) -> Action | None:
    head = _name(words[0], windows or simple.dialect == "windows")
    segment = _segment(words)
    is_win = windows or simple.dialect == "windows"
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
        return _python_c(words, simple, segment, is_win)
    if head in ("node", "nodejs", "deno", "bun"):
        return _node_e(words, simple, segment, is_win)
    return None


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


def _python_c(words, simple, segment, is_win):
    body = _dash_body(words, ("-c",))
    return _code_body(body, simple, segment, "python-c", _PY_DELETE, _PY_STOP, "python-os-kill")


def _node_e(words, simple, segment, is_win):
    body = _dash_body(words, ("-e", "--eval", "-p", "--print"))
    return _code_body(body, simple, segment, "node-e", _NODE_DELETE, _NODE_STOP, "node-process-kill")


def _dash_body(words, flags):
    rest = words[1:]
    for i, word in enumerate(rest):
        if word in flags:
            return rest[i + 1] if i + 1 < len(rest) else ""
        if word is None:
            return None  # `-c "$X"`: the script cannot be read, so no trigger is seen
    return None


def _code_body(body, simple, segment, rule, delete_re, stop_re, stop_rule):
    """A delete or stop inside a `-c`/`-e` body. Shapes are matched on the code with its string
    and comment contents blanked, so a delete word written inside a string or comment does not
    fire; a delete call's own string-literal argument is the target (a non-literal argument is
    unreadable, so None)."""

    if body is None:
        return None
    code = _blank_code(body)
    deletes = list(delete_re.finditer(code))
    if deletes:
        targets = tuple(_arg_target(body, m) for m in deletes)
        return Delete(targets, simple.cwd, True, True, True, rule, segment)
    stops = list(stop_re.finditer(code))
    if stops:
        pids = tuple(int(m.group(1)) for m in stops if m.group(1))
        opaque = any(not m.group(1) for m in stops)
        return _finish_stop(list(pids), [], opaque, True, stop_rule, segment)
    return None


def _arg_target(body: str, match: re.Match) -> str | None:
    """The literal path a delete call acts on, read from the original body at the call's
    position; None when its argument is not a plain string literal (a variable, a join)."""

    tail = body[match.start():]
    found = re.match(r"[^(]*\(\s*(['\"])((?:\\.|[^'\"\\])*)\1\s*[),]", tail)
    return found.group(2) if found else None


def _blank_code(body: str) -> str:
    """`body` with the contents of every string literal emptied and every `#` or `//` comment
    removed, so only real code is left to match shapes against."""

    out = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch in "'\"`":
            j = i + 1
            while j < n and body[j] != ch:
                j += 2 if body[j] == "\\" else 1
            out.append(ch + ch)  # an empty string of the same quote keeps the call shape
            i = j + 1
        elif ch == "#" or body[i:i + 2] == "//":
            while i < n and body[i] != "\n":
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _segment(words) -> str:
    text = " ".join("?" if w is None else w for w in words)
    return text[:MAX_SEGMENT]
