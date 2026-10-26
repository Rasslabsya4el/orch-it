"""The commands of the merge flow (#400):

- `pr open <ticket> --acceptance <file> [--branch B] [--base main] [--work-tree .] [--repo R]`: ORCH's
  acceptance step (decision 1, `orch_it.merge.pr_open`): the measured rows checked against the
  ticket's, the branch pushed, one pull request per ticket opened with `Refs #N` (or its own reused),
  the acceptance comment, the ready mark, auto-merge on, the ticket parked on the pull request; prints
  `pr #<n> opened|reused at <head>: <park line>`. `--branch` defaults to the work tree's branch;
- `follow [<pr>] [--event <file>] [--base main] [--repo R] [--role github]`: the ticket follows its
  pull request (decision 3, `orch_it.merge.follow`): the pull request named, or the one a GitHub event
  of the `follow` workflow is about; prints `#<pr> <outcome> ticket=#<n> <detail>` or `skipped: <why>`;
- `merge unlock [--repo R]`: remove the repo lock of the merge line, only when it is stale (older
  than `lock_stale_minutes`); prints whose it was and writes `lock.removed`. A lock that is not
  stale is never removed (`error: lock_not_stale`, naming its holder); one it cannot remove whole
  is `error: lock_unwritable` and still names its holder.

`--repo` defaults to the work tree's GitHub origin. A refusal prints `error: <code>` and exits 2.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from orch_it import settings
from orch_it.events.store import Store
from orch_it.merge import git as merge_git
from orch_it.merge import lock
from orch_it.merge.follow import FollowError, follow, pr_of_event
from orch_it.merge.gh import GhPullHub
from orch_it.merge.git import GitError
from orch_it.merge.pr_open import ROLE as ORCH, PrError, open_pr
from orch_it.merge.settings import MergeSettings
from orch_it.testruns.github import DEFAULT_REQUIRED_JOBS, REQUIRED_JOBS_SETTING
from orch_it.tickets.commands import OWNER_SETTING
from orch_it.tickets.gh_check import read_event
from orch_it.tickets.park import ROLE

GROUPS = ("merge", "pr", "follow")
ERRORS = (lock.LockError, PrError, FollowError)


def add_parsers(commands, common: argparse.ArgumentParser) -> None:
    repo = argparse.ArgumentParser(add_help=False, parents=[common])
    repo.add_argument("--repo", help="owner/name (default: the work tree's GitHub origin)")
    group = commands.add_parser("merge", help="the merge line").add_subparsers(dest="command", required=True)
    group.add_parser("unlock", parents=[repo], help="remove the repo lock of the merge line when it is stale")
    group = commands.add_parser("pr", help="the pull request of a ticket").add_subparsers(dest="command",
                                                                                          required=True)
    p = group.add_parser("open", parents=[repo], help="ORCH's acceptance step: one pull request per ticket")
    p.add_argument("ticket", type=int)
    p.add_argument("--acceptance", required=True, metavar="FILE",
                   help="the measured rows: a table with the columns row, result, evidence")
    p.add_argument("--branch", help="the ticket's branch (default: the work tree's)")
    p.add_argument("--base", default="main", help="the branch it merges into (default: main)")
    p.add_argument("--work-tree", default=".", metavar="PATH")
    p.add_argument("--role", default=ORCH)

    p = commands.add_parser("follow", parents=[repo], help="move a ticket as its pull request stands")
    p.add_argument("pr", nargs="?", type=int, help="the pull request (or give --event)")
    p.add_argument("--event", metavar="PATH", help="a GitHub event payload ($GITHUB_EVENT_PATH)")
    p.add_argument("--base", default="main", help="the branch of the merge line (default: main)")
    p.add_argument("--role", default="github")


def run(args, store_of: Callable[[], Store], repo_of: Callable[[str], str]) -> tuple[str, int]:
    now = datetime.now(UTC)
    if args.group == "pr":
        return _pr_open(args, store_of, repo_of, now), 0
    if args.group == "follow":
        return _follow(args, store_of, repo_of, now), 0
    repo = args.repo or repo_of(".")
    removed = lock.unlock_stale(repo, settings=MergeSettings.from_settings(settings.load(), repo), clock=lambda: now)
    held = removed.age_minutes(now)
    with store_of().writer() as writer:
        writer.write({"kind": "lock.removed", "repo": repo, "ticket": None, "role": ROLE, "session": None,
                      "parent_session": None, "turn": None, "step": None, "run": None, "note": None,
                      "detail": {"holder": removed.holder, "since": removed.since.isoformat(),
                                 "held_minutes": round(held, 1) if held != float("inf") else None}})
    return f"removed the stale lock of {repo}: {removed.said()}\n", 0


def _pr_open(args, store_of, repo_of, now: datetime) -> str:
    repo = args.repo or repo_of(args.work_tree)
    try:
        acceptance = Path(args.acceptance).read_text("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise PrError("acceptance_unreadable", f"{args.acceptance}: {e}") from None
    git = merge_git.work_tree(Path(args.work_tree))
    try:
        branch = args.branch or git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    except GitError as e:
        raise PrError("git_failed", str(e)) from None
    done = open_pr(GhPullHub(), store_of(), repo, args.ticket, acceptance=acceptance, branch=branch, base=args.base,
                   git=git, settings=MergeSettings.from_settings(settings.load(), repo), now=now, role=args.role)
    return f"pr #{done.pr} {'reused' if done.reused else 'opened'} at {done.head[:12]}: {done.park}\n"


def _follow(args, store_of, repo_of, now: datetime) -> str:
    repo = args.repo or repo_of(".")
    hub = GhPullHub()
    if (args.pr is None) == (args.event is None):
        raise FollowError("bad_options", "give a pull request or --event, not both")
    number, why = (args.pr, "") if args.pr is not None else pr_of_event(read_event(args.event), hub, repo)
    if number is None:
        return f"skipped: {why}\n"
    raw = settings.load()
    done = follow(hub, store_of(), repo, number, base=args.base, settings=MergeSettings.from_settings(raw, repo),
                  now=now, role=args.role, required=required_jobs(raw), owner=owner_login(raw))
    ticket = f"#{done.ticket}" if done.ticket else "-"
    return f"#{done.pr} {done.outcome} ticket={ticket} {done.detail}".rstrip() + "\n"


def required_jobs(raw: dict) -> tuple[str, ...]:
    """The required check names (the setting `github_required_jobs`, default `linux`, `windows`)."""

    jobs = raw.get(REQUIRED_JOBS_SETTING, DEFAULT_REQUIRED_JOBS)
    if not isinstance(jobs, list) or not jobs or not all(isinstance(job, str) and job for job in jobs):
        raise settings.SettingsError("bad_setting", REQUIRED_JOBS_SETTING)
    return tuple(jobs)


def owner_login(raw: dict) -> str | None:
    owner = raw.get(OWNER_SETTING)
    return owner if isinstance(owner, str) and owner else None

