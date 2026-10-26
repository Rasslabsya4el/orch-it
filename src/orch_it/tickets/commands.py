"""The `tickets` commands of the command line (#398 decisions 4 and 5):

- `tickets park <n> <kind> <ref> --until <YYYY-MM-DD> [--repo R] [--role script]`: the one way a
  ticket is parked: its `Parked:` line and the `parked` label in one write; prints the line;
- `tickets unpark <n> [--repo R]`: line and label off together, by hand;
- `tickets parks [--repo R]`: the reader: every parked ticket read, acted on and reported.

`--repo` defaults to the work tree's GitHub origin. A refusal is a `ParkError`: `error: <code>`.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from orch_it.events.store import QueryResult, Store
from orch_it.tickets.hub import GhHub
from orch_it.tickets.park import KINDS, park, unpark
from orch_it.tickets.reader import read_parks


def add_parsers(commands, common: argparse.ArgumentParser) -> None:
    group = commands.add_parser("tickets", help="the tickets of the layer").add_subparsers(
        dest="command", required=True)
    p = group.add_parser("park", parents=[common], help="park a ticket on one event until a date")
    p.add_argument("number", type=int)
    p.add_argument("kind", help=f"one of {', '.join(KINDS)}")
    p.add_argument("ref", help="the PR or ticket (#N, owner/repo#N), date, run id or version it waits on")
    p.add_argument("--until", required=True, metavar="YYYY-MM-DD", help="the last day it may wait")
    p.add_argument("--repo", help="owner/name (default: the work tree's GitHub origin)")
    p.add_argument("--role", default="script")
    p = group.add_parser("unpark", parents=[common], help="take a ticket's park off by hand")
    p.add_argument("number", type=int)
    p.add_argument("--repo", help="owner/name (default: the work tree's GitHub origin)")
    p.add_argument("--role", default="script")
    p = group.add_parser("parks", parents=[common], help="read every parked ticket and act on it")
    p.add_argument("--repo", help="owner/name (default: the work tree's GitHub origin)")
    p.add_argument("--role", default="script")


def run(args, store: Store, repo: str, render) -> str:
    now = datetime.now(UTC)
    if args.command == "park":
        made = park(GhHub(), store, repo, args.number, args.kind, args.ref, args.until, today=now.date(),
                    role=args.role)
        return made.line + "\n"
    if args.command == "unpark":
        taken = unpark(GhHub(), store, repo, args.number, now=now, role=args.role)
        return f"unparked: {taken.line if taken else 'a park that could not be read'}\n"
    reports = read_parks(GhHub(), store, repo, now=now, role=args.role)
    return render(QueryResult(["ticket", "outcome", "park", "detail"],
                              [(r.number, r.outcome, r.line, r.detail) for r in reports]))
