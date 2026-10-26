"""The git pre-push hook of #395 (decision 4, row 6): a push to a public repo is scanned for
private data, every route of a push at once (an agent, a person, any tool).

Git calls `pre-push <remote-name> <remote-url>` and writes one line per ref on stdin:
`<local ref> <local sha> <remote ref> <remote sha>`. For the commits the remote lacks, the
remote repo's visibility is read from the GitHub API at push time (public, or unknown or
unreachable, counts as public) and, when public, the commits go through the machine's patterns
(`scanner.Patterns.of_machine`). A hit refuses the push (exit 1) and names commit, place, file,
line and kind, never the value; each hit is a `guard.fired` deny and a scanned clean push is one
pass. A hook that crashes lets the push through and writes `error.raised` with `failed_open`
true (decision 7): a pre-push that always blocks would wedge every push. A scan that finds
something is the guard working, not a crash, and refuses even when the store cannot be written.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

from orch_it.guards import hook, scanner
from orch_it.testruns import github

GUARD = "private_data_public"
COMPONENT = "pre_push"
VISIBILITIES = ("public", "private", "internal")
# the visibilities a push may skip the scan for; anything else, unreadable included, is public
PRIVATE_VISIBILITIES = ("private", "internal")
EXIT_REFUSED = 1
# lines of hits printed to the person pushing; the rest is counted
SHOWN_HITS = 50
PATH_BOUND = 200

_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_GITHUB_REMOTE = re.compile(
    r"(?:https?://(?:[^/@]*@)?|ssh://(?:[^/@]*@)?|git://|git@)github\.com(?::\d+)?[/:]"
    r"([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", re.IGNORECASE)

Visibility = Callable[[str], str]


def github_repo_of(remote_url: str) -> str | None:
    """`owner/name` of a GitHub remote url (https, ssh or scp form), or None for any other remote."""

    found = _GITHUB_REMOTE.fullmatch(remote_url.strip()) if isinstance(remote_url, str) else None
    if not found or found.group(2) in (".", ".."):
        return None
    return f"{found.group(1)}/{found.group(2)}"


def visibility_of(remote_url: str, gh: Callable[[list[str]], str] | None = None) -> str:
    """The remote repo's visibility from the GitHub API now: `public`, `private` or `internal`,
    else `unknown` (not a GitHub remote, `gh` missing, unauthenticated, timed out, or an answer
    that is none of the three). Never raises; a remote that is no GitHub repo is not asked."""

    repo = github_repo_of(remote_url)
    if repo is None:
        return "unknown"
    try:
        answer = (gh or github.run_gh)(["api", f"repos/{repo}", "--jq", ".visibility"])
    except Exception:  # unreachable is public: whatever went wrong, the scan runs
        return "unknown"
    answer = answer.strip().lower() if isinstance(answer, str) else ""
    return answer if answer in VISIBILITIES else "unknown"


def _is_private(visibility: Visibility, remote_url: str) -> bool:
    try:
        answer = visibility(remote_url)
    except Exception:
        return False
    return isinstance(answer, str) and answer.strip().lower() in PRIVATE_VISIBILITIES


def _pushes(raw_stdin: str) -> list[tuple[str, str]]:
    """(local sha, remote sha) of each ref line git wrote, leaving out a delete (local sha all
    zeros) and any line that is not four fields with two full shas."""

    pushes = []
    for text in raw_stdin.splitlines():
        fields = text.split()
        if len(fields) != 4 or not all(_SHA.fullmatch(sha) for sha in (fields[1], fields[3])):
            continue
        if set(fields[1]) == {"0"}:
            continue
        pushes.append((fields[1], fields[3]))
    return pushes


def _new_commits(work_tree: Path, local: str, remote: str, remote_name: str) -> list[str]:
    """The commits `local` has that the remote lacks. A new ref (remote sha all zeros), or a remote
    sha this clone does not hold, takes every commit reachable from `local` that no remote-tracking
    ref of this remote holds."""

    if set(remote) != {"0"}:
        try:
            return scanner.commits_of_range(work_tree, f"{remote}..{local}")
        except scanner.ScanError as e:
            if e.code not in ("bad_rev", "git_failed"):
                raise
    if remote_name:
        return scanner.commits_of_revs(work_tree, local, "--not", f"--remotes={remote_name}")
    return scanner.commits_of_revs(work_tree, local)


def _where(hit: scanner.Hit) -> str:
    line = "-" if hit.line is None else hit.line
    file = "-" if hit.file is None else hit.file.replace("\n", "\\n").replace("\r", "\\r")
    return f"{file}:{line}"


def _fired(hit: scanner.Hit) -> hook.Fired:
    where = _where(hit)
    return hook.Fired(GUARD, "deny", hit.kind, f"{hit.commit[:12]} {hit.where} {where}"[:120],
                      where[:PATH_BOUND] if hit.file is not None else None)


def _refusal(hits: list[scanner.Hit]) -> str:
    lines = ["orch-it: push refused, the commits hold private data and the remote is public"
             " (or its visibility could not be read):"]
    lines += [f"  {hit.commit[:12]} {hit.where} {_where(hit)} {hit.kind}" for hit in hits[:SHOWN_HITS]]
    if len(hits) > SHOWN_HITS:
        lines.append(f"  ... and {len(hits) - SHOWN_HITS} more")
    lines.append("take the values out of the commits (amend or rebase) and push again;"
                 " `python -m orch_it guards scan --range <remote>..<local>` lists the same")
    return "\n".join(lines) + "\n"


def _say(text: str) -> None:
    """To stderr; a console that cannot show a file name's letters shows them escaped."""

    try:
        encoding = sys.stderr.encoding or "utf-8"
        sys.stderr.write(text.encode(encoding, "backslashreplace").decode(encoding))
        sys.stderr.flush()
    except Exception:  # the refusal is the exit code; a stderr that is gone does not undo it
        pass


def run_pre_push(raw_stdin: str, store, *, remote_url: str, remote_name: str,
                 work_tree: Path | str = ".", visibility: Visibility | None = None,
                 patterns: scanner.Patterns | None = None) -> tuple[str, int]:
    """Decide one push. `raw_stdin`: git's lines; `visibility`: the remote url -> `public`,
    `private`, `internal` or anything else for unknown (default: the GitHub API); `patterns`: what
    counts as private (default: this machine's). Returns ("", exit code): 1 when the push is
    refused (the refusal is printed to stderr), else 0. Never raises."""

    work_tree = Path(work_tree)
    try:
        repo = github_repo_of(remote_url) or hook._repo_of(str(work_tree))
    except Exception:
        repo = hook.DEFAULT_REPO
    try:
        commits = list(dict.fromkeys(
            sha for local, remote in _pushes(raw_stdin)
            for sha in _new_commits(work_tree, local, remote, remote_name)))
        if not commits or _is_private(visibility or visibility_of, remote_url):
            return "", 0
        if patterns is None:
            patterns = scanner.Patterns.of_machine(allowed_emails=_profile_emails(repo))
        hits = scanner.scan_commits(work_tree, commits, patterns)
    except Exception as exc:  # a guard that crashes lets the push through, and says so
        _write_crash(store, exc, repo)
        return "", 0
    fired = ([_fired(hit) for hit in hits] if hits
             else [hook.Fired(GUARD, "pass", "clean", f"{len(commits)} commits scanned", None)])
    try:
        hook._write_events(store, hook.Decision(bool(hits), None, fired), repo=repo, session=None)
    except Exception:  # the store never decides a push
        pass
    if hits:
        _say(_refusal(hits))
        return "", EXIT_REFUSED
    return "", 0


def _profile_emails(repo: str) -> tuple[str, ...]:
    """The public profile emails that are NOT private data (decision 4): the authenticated
    account's and the repo owner's, read from GitHub now; a reader that fails adds nothing, so
    at worst that address is treated as private. The scanner already exempts noreply addresses."""

    logins = ["user"]  # `gh api user` is the authenticated account
    owner = repo.split("/", 1)[0] if "/" in repo else ""
    if owner:
        logins.append(f"users/{owner}")
    found: list[str] = []
    for endpoint in logins:
        try:
            email = github.run_gh(["api", endpoint, "--jq", ".email"]).strip()
        except github.GithubError:
            continue
        if email and email != "null" and "@" in email:
            found.append(email)
    return tuple(found)


def _write_crash(store, exc: BaseException, repo: str) -> None:
    try:
        hook._safe_write_error(store, COMPONENT, getattr(exc, "code", type(exc).__name__),
                               repo=repo, session=None)
    except Exception:
        pass
