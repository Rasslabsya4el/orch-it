"""The command line: `python -m orch_it events <command>` (#393 decisions 8 and 9; #394 decisions
7 and 9 for `import-github` and `test-places`).

Commands print a result as an aligned text table (header, then rows). Any coded error of the
store, the settings, the importer, a named query or a command prints `error: <code>` (plus the
field or detail) to stderr and the exit code is 2.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from orch_it import settings
from orch_it.events import queries, schema, transcripts
from orch_it.events.store import QueryResult, Store, StoreError
from orch_it.testruns import github

DEFAULT_REPO = "Rasslabsya4el/orch-it"
EXIT_ERROR = 2


_INTEGER = re.compile(r"-?(0|[1-9][0-9]*)")
_DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)\.[0-9]+")


class CommandError(Exception):
    """A command given a value it cannot use. `code`: `bad_pct` (a weekly percent outside
    0-100)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def _param(text: str) -> tuple[str, int | float | str]:
    """`name=value`; the value binds as a number only when written plainly as one (`7`,
    `-2.5`), so `007`, `1_000` or `nan` stay text."""

    name, equals, value = text.partition("=")
    if not equals or not name:
        raise argparse.ArgumentTypeError(f"expected name=value, got {text!r}")
    if _INTEGER.fullmatch(value):
        return name, int(value)
    if _DECIMAL.fullmatch(value):
        return name, float(value)
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

    p = command("session-cost", "$, %% week, tokens and minutes per session and model")
    p.add_argument("--session")
    p.add_argument("--repo")
    p.add_argument("--since", metavar="ISO")
    p.add_argument("--until", metavar="ISO")

    command("limit-rate", "dollars per 1 %% of the weekly limit between readings")

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

    p = command("import-github", "import the runs of the repo's `tests` workflow from GitHub")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--since", metavar="ISO")

    p = command("test-places", "runs, outcomes and median run and GitHub queue minutes per place and OS")
    p.add_argument("--repo")
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
        if not 0 <= args.pct <= 100:
            raise CommandError("bad_pct", str(args.pct))
        with _store(args).writer() as writer:
            writer.write({
                "kind": "value.measured", "repo": args.repo, "ticket": None, "role": args.role,
                "session": None, "parent_session": None, "turn": None, "step": None, "run": None,
                "note": args.note,
                "detail": {"name": "weekly_limit_pct", "value": args.pct, "unit": "%",
                           "source": "usage_card"}})
        return ""
    if command == "set-limit-rate":
        settings.save(queries.WEEKLY_LIMIT_SETTING,
                      queries.checked_rate(args.usd_per_pct, args.plan, args.measured))
        return ""
    if command == "import":
        done = transcripts.import_transcripts(
            Path(args.folder), _store(args), repo=args.repo, role=args.role,
            subagent_role=args.subagent_role, ticket=args.ticket, session=args.session,
            quiet_minutes=args.quiet_minutes)
        return (f"written={done.written} already_stored={done.already_stored}"
                f" sessions={done.sessions} bad_lines={done.bad_lines}"
                f" pending_lines={done.pending_lines} refused_events={done.refused_events}"
                f" unreadable_files={done.unreadable_files}\n")
    if command == "import-github":
        done = github.import_github(_store(args), args.repo, since=args.since)
        failed = f" failure_codes={','.join(done.failure_codes)}" if done.failure_codes else ""
        return (f"runs_read={done.runs_read} jobs={done.jobs} written={done.written}"
                f" already_stored={done.already_stored} reports_missing={done.reports_missing}"
                f" runs_failed={done.runs_failed}{failed}\n")
    if command == "test-places":
        return render(queries.test_places(_store(args), args.repo))
    raise AssertionError(command)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_ERROR
    try:
        text = _run(args)
    except (schema.EventError, StoreError, settings.SettingsError, transcripts.ImportError_,
            queries.QueryError, github.GithubError, CommandError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    sys.stdout.write(text)
    return 0
