"""The command line: `python -m orch_it events <command>` (#393 decisions 8 and 9; #394 decisions
7 and 9 for `import-github` and `test-places`), `python -m orch_it tests run` (#394 decision 9);
`python -m orch_it labels sync` (#398 decision 6); `python -m orch_it guards scan` (#395 decision 4:
it prints one line per private value found and exits 1 when there is any); `python -m orch_it skills
shadowed` (#397 decision 3), a session-start hook that exits 0 whatever happens; `python -m orch_it
research <brief>` and `research-mcp` (#396 decision 1, the research bridge's two fronts); `python -m
orch_it skills check` and `skills update` (#397 decision 4).

Commands print a result as an aligned text table (header, then rows). Any coded error of the
store, the settings, the importer, a named query or a command prints `error: <code>` (plus the
field or detail) to stderr and the exit code is 2.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from orch_it import labels, settings
from orch_it.events import queries, schema, transcripts
from orch_it.events.store import QueryResult, Store, StoreError
from orch_it.guards import scanner
from orch_it.research import mcp as research_mcp
from orch_it.research import run as research_run
from orch_it.research.settings import ResearchRefused, ResearchSettings
from orch_it.skills import shadow, update
from orch_it.skills.copies import SkillsError
from orch_it.skills.forge import GithubForge
from orch_it.testruns import github
from orch_it.testruns.config import TestSettings
from orch_it.testruns.runner import NEED_OSES, RunError, git_output, run_tests

DEFAULT_REPO = "Rasslabsya4el/orch-it"
EXIT_ERROR = 2
EXIT_INTERRUPTED = 130
# `tests run` exits 0 on passed, 1 on failed, 3 on any other outcome (no result, refused, lost,
# stopped): a script tells "red" from "no answer" without reading the line
TESTS_EXIT = {"passed": 0, "failed": 1}
TESTS_EXIT_OTHER = 3
# `research` exits the same way: 0 passed, 1 failed (a reply that is no report), 3 no result
RESEARCH_EXIT = TESTS_EXIT


_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_INTEGER = re.compile(r"-?(0|[1-9][0-9]*)")
_DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)\.[0-9]+")


class CommandError(Exception):
    """A command given a value it cannot use. `code`: `bad_pct` (a weekly percent outside
    0-100), `repo_unknown` (no `--repo` and no GitHub origin in the work tree), `bad_options`
    (`guards scan` options that do not go together)."""

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

    tests = commands.add_parser("tests", help="test runs").add_subparsers(dest="command", required=True)
    p = tests.add_parser("run", parents=[common], help="run the repo's tests at a test place")
    p.add_argument("--os", default="any", choices=NEED_OSES,
                   help="the OS the run needs (default: any)")
    p.add_argument("--ids-file", metavar="PATH", help="run only the tests named in this file")
    p.add_argument("--place", help="force a place (own, machine:<id>, github); default: the place rule")
    p.add_argument("--work-tree", default=".", metavar="PATH")
    p.add_argument("--repo", help="owner/name (default: from the work tree's origin)")
    p.add_argument("--ticket", type=int)
    p.add_argument("--role", default="script")

    guards = commands.add_parser("guards", help="guards of the public repo").add_subparsers(
        dest="command", required=True)
    p = guards.add_parser("scan", help="look for private data in commits or in a tree")
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument("--range", dest="rev_range", metavar="A..B", help="the commits of a range")
    what.add_argument("--tree", action="store_true", help="every tracked file at --rev")
    what.add_argument("--all", action="store_true", help="every commit reachable from HEAD")
    p.add_argument("--rev", metavar="REV", help="the revision --tree reads (default: HEAD)")
    p.add_argument("--work-tree", default=".", metavar="PATH")
    p.add_argument("--machine-independent", action="store_true",
                   help="only what holds on any machine: no home, host name or word of this one")
    p.add_argument("--allow-email", action="append", default=[], metavar="ADDR",
                   help="an address that is no hit")
    p.add_argument("--allow-email-of", action="append", default=[], metavar="LOGIN",
                   help="allow the public email of this GitHub account")
    p.add_argument("--words-file", metavar="PATH",
                   help="the private words (default: the setting `private_words_file`)")

    label_commands = commands.add_parser("labels", help="the layer's labels").add_subparsers(
        dest="command", required=True)
    p = label_commands.add_parser("sync", parents=[common],
                                  help="make a repo's labels match the layer's set")
    p.add_argument("--repo", help="owner/name (default: the work tree's GitHub origin)")

    skills = commands.add_parser("skills", help="the repo's skill copies").add_subparsers(
        dest="command", required=True)
    skills.add_parser("shadowed", parents=[common],
                      help="the session-start hook: a skill.shadowed event per personal skill of a repo skill's name")
    p = skills.add_parser("check", parents=[common],
                          help="fetch his upstream and give each skill its class against the manifest")
    p.add_argument("--ref", default="main", help="his branch (default: main)")
    p.add_argument("--work-tree", default=".", metavar="PATH")
    p = skills.add_parser("update", parents=[common],
                          help="on a change, one pull request with the new copies and every skill's class")
    p.add_argument("--ref", default="main", help="his branch (default: main)")
    p.add_argument("--work-tree", default=".", metavar="PATH")
    p.add_argument("--repo", help="owner/name (default: the work tree's GitHub origin)")
    p.add_argument("--dispatch-tests", action="store_true",
                   help="start the tests workflow on the branch (a workflow token's pull request starts none)")

    p = commands.add_parser("research", parents=[common], help="run a brief through the research bridge")
    p.add_argument("brief", help="the brief file (Markdown); the report goes to the path it names")
    p.add_argument("--model", help=f"a model alias of the setting research.models: {_aliases()} "
                                   "(default: by brief size)")
    p.add_argument("--no-reddit", action="store_true", help="primary sources: no Reddit link required")
    p.add_argument("--report", metavar="PATH", help="write the report here instead of the brief's path")
    p.add_argument("--work-tree", default=".", metavar="PATH")
    p.add_argument("--repo", help="owner/name (default: from the work tree's origin)")
    p.add_argument("--ticket", type=int)
    p.add_argument("--done-file", metavar="PATH", help=argparse.SUPPRESS)  # the MCP front's job answer
    p.add_argument("--job", help=argparse.SUPPRESS)  # the MCP front's job id, carried by the started event

    commands.add_parser("research-mcp", help="the research bridge's MCP server on stdin and stdout")
    return root


def _aliases() -> str:
    """The aliases the settings name, for the help text; a settings file that cannot be read
    leaves the help plain (the command then says what is wrong)."""

    try:
        return ", ".join(ResearchSettings.from_settings(settings.load()).models) or "none set"
    except settings.SettingsError:
        return "see the settings"


def render(result: QueryResult) -> str:
    """The result as text: a header line, then one line per row, columns aligned."""

    table = [list(result.columns)]
    table += [["NULL" if value is None else str(value) for value in row] for row in result.rows]
    widths = [max(len(line[i]) for line in table) for i in range(len(result.columns))]
    return "".join("  ".join(cell.ljust(w) for cell, w in zip(line, widths)).rstrip() + "\n"
                   for line in table)


def _store(args) -> Store:
    return Store.open(args.store)


_ORIGIN = re.compile(r"(?:github\.com[:/])([A-Za-z0-9-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def _repo_of(work_tree: str) -> str:
    """`owner/name` of the work tree's `origin` on GitHub, or `CommandError("repo_unknown")`."""

    origin = git_output(work_tree, "remote", "get-url", "origin")
    found = _ORIGIN.search(origin) if origin else None
    if not found:
        raise CommandError("repo_unknown", f"{work_tree}: give --repo")
    return found.group(1)


def _tests_run(args) -> tuple[str, int]:
    repo = args.repo or _repo_of(args.work_tree)
    test_settings = TestSettings.from_settings(settings.load(), repo)
    done = run_tests(_store(args), work_tree=Path(args.work_tree), repo=repo, settings=test_settings,
                     ids_file=Path(args.ids_file) if args.ids_file else None, place=args.place,
                     need_os=args.os, role=args.role, ticket=args.ticket)
    line = (f"run={done.run} place={done.place} outcome={done.outcome} reason={done.reason or '-'}"
            f" collected={done.collected} failed={done.failed}\n")
    return line, TESTS_EXIT.get(done.outcome, TESTS_EXIT_OTHER)


def _email_of(login: str) -> str | None:
    """The public email of a GitHub account, or None (with a warning) when it cannot be read or
    the account shows none."""

    email = None
    if _LOGIN.fullmatch(login):
        try:
            email = github.run_gh(["api", f"users/{login}", "--jq", ".email"]).strip()
        except github.GithubError:
            pass
    if not email or email == "null" or "@" not in email:
        print(f"warning: email_of_unreadable: {login}", file=sys.stderr)
        return None
    return email


def _guards_scan(args) -> tuple[str, int]:
    """`guards scan`: one line per hit, then `hits=<n> commits=<n>`; exit 0 with no hit, 1 with hits."""

    if args.rev and not args.tree:
        raise CommandError("bad_options", "--rev goes with --tree")
    if args.words_file and args.machine_independent:
        raise CommandError("bad_options", "--words-file goes with the machine's own patterns")
    allowed = [*args.allow_email, *filter(None, map(_email_of, args.allow_email_of))]
    if args.machine_independent:
        patterns = scanner.Patterns.machine_independent(allowed_emails=allowed)
    else:
        patterns = scanner.Patterns.of_machine(
            allowed_emails=allowed, words_file=Path(args.words_file) if args.words_file else None)
    work_tree = Path(args.work_tree)
    if args.tree:
        hits, commits = scanner.scan_tree(work_tree, patterns, args.rev or "HEAD"), 1
    else:
        shas = scanner.commits_of_range(work_tree, "HEAD" if args.all else args.rev_range)
        hits, commits = scanner.scan_commits(work_tree, shas, patterns), len(shas)
    lines = [f"{hit.commit[:12]} {hit.where} {_one_line(hit.file)}:"
             f"{'-' if hit.line is None else hit.line} {hit.kind}\n" for hit in hits]
    text = "".join(lines) + f"hits={len(hits)} commits={commits}\n"
    # a console that cannot show a file name's letters shows them escaped, not an error
    text = text.encode(sys.stdout.encoding or "utf-8", "backslashreplace").decode(
        sys.stdout.encoding or "utf-8")
    return text, 1 if hits else 0


def _one_line(path: str | None) -> str:
    return "-" if path is None else path.replace("\n", "\\n").replace("\r", "\\r")


def _labels_sync(args) -> str:
    done = labels.sync(args.repo or _repo_of("."))
    return render(QueryResult(["label", "action"], done))


def _research(args) -> tuple[str, int]:
    try:
        brief = Path(args.brief).read_text("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise ResearchRefused("brief_unreadable", f"{args.brief}: {e}") from None
    repo = args.repo or research_run.repo_of(Path(args.work_tree))
    done = research_run.research(
        brief, settings=ResearchSettings.from_settings(settings.load()), store=_store(args), repo=repo,
        work_tree=Path(args.work_tree), brief_name=Path(args.brief).stem, model=args.model,
        no_reddit=args.no_reddit, report_to=args.report, ticket=args.ticket, job=args.job)
    if args.done_file:
        research_run.write_done(Path(args.done_file), done)
    return done.line(), RESEARCH_EXIT.get(done.outcome, TESTS_EXIT_OTHER)


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


def _repo_or_none(work_tree: Path) -> str | None:
    try:
        return _repo_of(str(work_tree))
    except CommandError:
        return None


def _skills_shadowed(args) -> int:
    """The SessionStart hook (#397 decision 3): prints nothing and exits 0 whatever happens, so it
    never blocks a session; an error goes to stderr."""

    try:
        shadow.run_hook(sys.stdin.buffer.read(), open_store=lambda: _store(args), repo_of=_repo_or_none)
    except Exception as e:  # fail open: a broken hook must never stop a session
        print(f"error: {e}", file=sys.stderr)
    return 0


def _skills(args) -> str:
    if args.command == "check":
        return update.render(update.check(Path(args.work_tree), ref=args.ref))
    repo = args.repo or _repo_of(args.work_tree)
    done = update.update(Path(args.work_tree), forge=GithubForge(repo), ref=args.ref,
                         dispatch_tests=args.dispatch_tests)
    if done.status == "no_change":
        return "no change\n"
    rule = f"labels={','.join(done.labels)}" if done.labels else "merges when green"
    if done.status == "already_open":
        return f"pull request already open: {done.pr} {rule}\n"
    if done.status == "declined":
        return f"pull request closed unmerged for this commit, not opened again: {done.pr}\n"
    return f"pull request: {done.pr} {rule}\n"


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_ERROR
    if args.group == "skills" and args.command == "shadowed":
        return _skills_shadowed(args)
    try:
        if args.group == "tests":
            text, code = _tests_run(args)
        elif args.group == "guards":
            text, code = _guards_scan(args)
        elif args.group == "labels":
            text, code = _labels_sync(args), 0
        elif args.group == "skills":
            text, code = _skills(args), 0
        elif args.group == "research":
            text, code = _research(args)
        elif args.group == "research-mcp":
            return research_mcp.serve()
        else:
            text, code = _run(args), 0
    except (schema.EventError, StoreError, settings.SettingsError, transcripts.ImportError_,
            queries.QueryError, github.GithubError, labels.LabelError, SkillsError, CommandError,
            RunError, scanner.ScanError, ResearchRefused) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # a run stopped by a person has already written its end
        print("error: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    sys.stdout.write(text)
    return code
