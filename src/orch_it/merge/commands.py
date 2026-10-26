"""The commands of the merge flow (#400):

- `merge unlock [--repo R]`: remove the repo lock of the merge line, only when it is stale (older
  than `lock_stale_minutes`); prints whose it was and writes `lock.removed`. A lock that is not
  stale is never removed (`error: lock_not_stale`, naming its holder); one it cannot remove whole
  is `error: lock_unwritable` and still names its holder.

`--repo` defaults to the work tree's GitHub origin. A refusal is a `LockError`: `error: <code>`.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC, datetime

from orch_it import settings
from orch_it.events.store import Store
from orch_it.merge import lock
from orch_it.merge.settings import MergeSettings
from orch_it.tickets.park import ROLE

GROUP = "merge"


def add_parsers(commands, common: argparse.ArgumentParser) -> None:
    repo = argparse.ArgumentParser(add_help=False, parents=[common])
    repo.add_argument("--repo", help="owner/name (default: the work tree's GitHub origin)")
    group = commands.add_parser(GROUP, help="the merge line").add_subparsers(dest="command", required=True)
    group.add_parser("unlock", parents=[repo], help="remove the repo lock of the merge line when it is stale")


def run(args, store_of: Callable[[], Store], repo_of: Callable[[], str]) -> tuple[str, int]:
    repo = args.repo or repo_of()
    now = datetime.now(UTC)
    removed = lock.unlock_stale(repo, settings=MergeSettings.from_settings(settings.load(), repo), clock=lambda: now)
    held = removed.age_minutes(now)
    with store_of().writer() as writer:
        writer.write({"kind": "lock.removed", "repo": repo, "ticket": None, "role": ROLE, "session": None,
                      "parent_session": None, "turn": None, "step": None, "run": None, "note": None,
                      "detail": {"holder": removed.holder, "since": removed.since.isoformat(),
                                 "held_minutes": round(held, 1) if held != float("inf") else None}})
    return f"removed the stale lock of {repo}: {removed.said()}\n", 0
