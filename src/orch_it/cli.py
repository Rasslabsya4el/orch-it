"""The command line: `python -m orch_it events <command>` (#393 decisions 8 and 9).

Commands print a result as an aligned text table (header, then rows). A `EventError`,
`StoreError` or `SettingsError` prints `error: <code>` (plus the field or detail) to stderr and
the exit code is 2.
"""

from __future__ import annotations

import argparse
import importlib
import math
import sys
from pathlib import Path

from orch_it import settings
from orch_it.events import queries, schema
from orch_it.events.store import QueryResult, Store, StoreError

DEFAULT_REPO = "Rasslabsya4el/orch-it"
EXIT_ERROR = 2


def _param(text: str) -> tuple[str, int | float | str]:
    name, equals, value = text.partition("=")
    if not equals or not name:
        raise argparse.ArgumentTypeError(f"expected name=value, got {text!r}")
    for kind in (int, float):
        try:
            return name, kind(value)
        except ValueError:
            pass
    return name, value


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m orch_it")
    root.add_argument("--store", metavar="PATH", help="the store folder (default: the `store` setting)")
    commands = root.add_subparsers(dest="group", required=True)
    events = commands.add_parser("events", help="the event store").add_subparsers(
        dest="command", required=True)

    # the store option is also taken after the command; it never overrides one given before it
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", metavar="PATH", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    def command(name, help_text):
        return events.add_parser(name, parents=[common], help=help_text)

    p = command("query", "run read-only SQL over the table `events`")
    p.add_argument("sql")
    p.add_argument("--param", action="append", type=_param, default=[], metavar="NAME=VALUE",
                   help="bind :NAME in the SQL (a number when it reads as one)")

    p = command("ticket-cost", "cost of a ticket from ready to close, per role")
    p.add_argument("--repo", required=True)
    p.add_argument("--ticket", required=True, type=int)

    p = command("session-cost", "$, % week, tokens and minutes per session and model")
    p.add_argument("--session")
    p.add_argument("--repo")
    p.add_argument("--since", metavar="ISO")
    p.add_argument("--until", metavar="ISO")

    command("limit-rate", "dollars per 1 % of the weekly limit between readings")

    p = command("measure-limit", "record a reading of the usage card's weekly percent")
    p.add_argument("pct", type=float)
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--role", default="owner")
    p.add_argument("--note")

    p = command("set-limit-rate", "save the setting weekly_limit_usd_per_pct")
    p.add_argument("usd_per_pct", type=float)
    p.add_argument("--plan", required=True)
    p.add_argument("--measured", required=True, metavar="DATE")

    p = command("import", "import a folder of Claude Code transcripts")
    p.add_argument("folder")
    p.add_argument("--repo", required=True)
    p.add_argument("--role", default="worker")
    p.add_argument("--subagent-role")
    p.add_argument("--ticket", type=int)
    p.add_argument("--session")
    p.add_argument("--quiet-minutes", type=float, default=30)
    return root


def render(result: QueryResult) -> str:
    """The result as text: a header line, then one line per row, columns aligned."""

    table = [list(result.columns)]
    table += [["NULL" if value is None else str(value) for value in row] for row in result.rows]
    widths = [max(len(line[i]) for line in table) for i in range(len(result.columns))]
    return "".join("  ".join(cell.ljust(w) for cell, w in zip(line, widths)).rstrip() + "\n"
                   for line in table)


def _store(args) -> Store:
    return Store.open(args.store)


def _run(args) -> str:
    command = args.command
    if command == "query":
        return render(_store(args).query(args.sql, dict(args.param)))
    if command == "ticket-cost":
        return render(queries.ticket_cost(_store(args), args.repo, args.ticket))
    if command == "session-cost":
        return render(queries.session_cost(_store(args), session=args.session, repo=args.repo,
                                           since=args.since, until=args.until))
    if command == "limit-rate":
        return render(queries.limit_rate(_store(args)))
    if command == "measure-limit":
        with _store(args).writer() as writer:
            writer.write({
                "kind": "value.measured", "repo": args.repo, "ticket": None, "role": args.role,
                "session": None, "parent_session": None, "turn": None, "step": None, "run": None,
                "note": args.note,
                "detail": {"name": "weekly_limit_pct", "value": args.pct, "unit": "%",
                           "source": "usage_card"}})
        return ""
    if command == "set-limit-rate":
        if not (math.isfinite(args.usd_per_pct) and args.usd_per_pct > 0
                and args.plan.strip() and args.measured.strip()):
            raise settings.SettingsError("bad_setting", queries.WEEKLY_LIMIT_SETTING)
        settings.save(queries.WEEKLY_LIMIT_SETTING, {
            "usd_per_pct": args.usd_per_pct, "plan": args.plan, "measured": args.measured})
        return ""
    if command == "import":
        # the importer is a module of its own part; it is looked for only when this command runs
        transcripts = importlib.import_module("orch_it.events.transcripts")
        done = transcripts.import_transcripts(
            Path(args.folder), _store(args), repo=args.repo, role=args.role,
            subagent_role=args.subagent_role, ticket=args.ticket, session=args.session,
            quiet_minutes=args.quiet_minutes)
        return (f"written={done.written} already_stored={done.already_stored}"
                f" sessions={done.sessions} bad_lines={done.bad_lines}"
                f" pending_lines={done.pending_lines}\n")
    raise AssertionError(command)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_ERROR
    try:
        text = _run(args)
    except (schema.EventError, StoreError, settings.SettingsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    sys.stdout.write(text)
    return 0
