"""One research run, start to end, the one bridge both fronts call (#396 decisions 1-6).

`research` refuses before any server starts what the settings cannot serve (an alias not in
`research.models`, the key's variable unset, no opencode, a report path outside the work tree),
then writes the `research_run` `step.started`, starts a private opencode server for this run
alone (its own port, password and state folder `<state>/runs/<run>/`, an empty work folder the
model is pointed at), sends the brief with the report tail, and waits. While it waits it writes a
`step.progressed` every `PROGRESS_SECONDS` (steps, sources, Reddit reached, quiet seconds, helper
sessions, read over the whole session tree). The run ends when the session goes idle (seen busy
once, or after the grace a fresh prompt needs), when the time bound passes (`minutes`; the wall
or the monotonic clock, whichever says so first), or when no new step came over the whole tree
for `stall_minutes`. A bounded run is aborted; either way the server's whole process tree is
killed on the way out.

Then the report is chosen from every assistant message (`report.choose`), the tree metered
(`usage.meter`), and the files written: a passed report to the path the brief names (else
`<run folder>/report.md`); for a run with no report, what the model wrote so far beside it as
`<name>.partial.md`. The `step.ended` carries the outcome: `passed`, `failed` (a reply that
fails the report check: `too_short`, `few_links`, `no_reddit`) or `no_result` (`time_bound`,
`stalled`, `server_failed`, `model_error`, `no_reply`, `empty`); a run stopped by a person
ends `stopped` and the interrupt goes on.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from orch_it.events.ids import live_id
from orch_it.events.store import Store
from orch_it.research import report, usage
from orch_it.research.opencode import OpencodeError, Server, find_opencode
from orch_it.research.settings import AGENT, ResearchRefused, ResearchSettings

ROLE = "external-researcher"
POLL_SECONDS = 2.0
PROGRESS_SECONDS = 15.0
GRACE_SECONDS = 10.0  # `prompt_async` answers before the work starts: idle means done only after this
REPORT_NAME = re.compile(r"`(docs/research/[^`\s]+\.md)`")

TAIL = (
    "\n\nUse live web search for every question; open Reddit threads (reddit.com / old.reddit.com), "
    "the wiki and forum posts, not tooltips alone. Report format, per question: the VERBATIM passages "
    "that answer it, each with its URL, the post date and who says it, the conditions the sources "
    "attach, where sources disagree both sides quoted, then one line 'what it says' with no "
    "recommendation. Do not write any other file: put the complete final report in this message, "
    "in markdown."
)
TAIL_NO_REDDIT = TAIL.replace(
    "open Reddit threads (reddit.com / old.reddit.com), the wiki and forum posts",
    "open the primary sources (papers, official docs, engineering blogs, the wiki)")


@dataclass(frozen=True)
class Result:
    """What a run ended with: the same shape from both fronts."""

    run: str
    outcome: str
    reason: str | None
    model_alias: str
    model: str
    report_path: str | None  # the report written; None when there is none
    partial_path: str | None  # what the model wrote, for a run with no report
    report_from: str | None
    report_chars: int
    links: int
    reddit_urls: int
    last_chars: int
    compactions: int
    minutes: float
    opencode_session: str | None
    tokens: dict = field(default_factory=dict)  # the tree's usage, as the ended event carries it

    def as_dict(self) -> dict:
        return asdict(self)


def report_path_of(brief: str, work_tree: Path, given: str | Path | None = None) -> Path | None:
    """Where the report goes: `given` (relative to the work tree), else the last
    `docs/research/<name>.md` path the brief names in backticks (a brief's own folder `briefs/`
    left out), else None. A path that leaves the work tree is refused."""

    if given is None:
        named = [found for found in REPORT_NAME.findall(brief) if "/briefs/" not in found]
        if not named:
            return None
        given = named[-1]
    root = Path(work_tree).resolve()
    path = (root / given).resolve()
    if Path(given).is_absolute():
        return path
    if path != root and root not in path.parents:
        raise ResearchRefused("report_path_outside", str(given))
    return path


def research(brief: str, *, settings: ResearchSettings, store: Store, repo: str, work_tree: Path,
             brief_name: str = "brief", model: str | None = None, no_reddit: bool = False,
             report_to: str | Path | None = None, ticket: int | None = None, env=None,
             poll_seconds: float = POLL_SECONDS, progress_seconds: float = PROGRESS_SECONDS,
             grace_seconds: float = GRACE_SECONDS, wall=time.time) -> Result:
    env = dict(os.environ if env is None else env)
    alias = model or settings.alias_for(len(brief))
    model_id = settings.model_id(alias)
    settings.key(env)
    try:
        argv = find_opencode(list(settings.opencode) if isinstance(settings.opencode, tuple) else settings.opencode)
    except OpencodeError as e:
        raise ResearchRefused("opencode_missing", str(e)) from None
    target = report_path_of(brief, work_tree, report_to)
    config = settings.opencode_config()
    provider_id = next(iter(config["provider"]))

    run = live_id()
    folder = settings.state / "runs" / run
    work = folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    target = target or folder / "report.md"
    envelope = {"repo": repo, "ticket": ticket, "role": ROLE, "session": None, "parent_session": None,
                "turn": None, "step": "research_run", "run": run, "note": None}
    started_mono, started_wall = time.monotonic(), wall()
    with store.writer() as writer:
        writer.write({"kind": "step.started", **envelope, "detail": {
            "brief": brief_name, "brief_chars": len(brief),
            "brief_sha": hashlib.sha256(brief.encode("utf-8")).hexdigest()[:12],
            "model_alias": alias, "model": model_id, "no_reddit": no_reddit, "helpers": settings.helpers}})
        session = None
        bounded = None
        messages: list = []
        tree = usage.TreeUsage(usage.Usage(), usage.Usage(), 0, 0, 0, 0)
        server_error = None
        try:
            with Server(argv, config=config, env=env, state_dir=folder, work_dir=work) as client:
                session = client.create_session(f"orch-it research {brief_name}")
                client.prompt(session, agent=AGENT, provider_id=provider_id, model_id=model_id,
                              text=brief + (TAIL_NO_REDDIT if no_reddit else TAIL))
                bounded = _wait(client, session, settings, writer, envelope, poll_seconds=poll_seconds,
                                progress_seconds=progress_seconds, grace_seconds=grace_seconds, wall=wall)
                if bounded:
                    try:
                        client.abort(session)
                    except OpencodeError:
                        pass  # the server goes down with its tree on the way out anyway
                messages = client.messages(session)
                tree = usage.meter(session, children=client.children, messages=client.messages,
                                   read_errors=(OpencodeError,))
        except OpencodeError as e:
            server_error = e
        except KeyboardInterrupt:
            writer.write({"kind": "step.ended", **envelope, "detail": {
                "outcome": "stopped", "minutes": _minutes(started_mono, started_wall, wall),
                "opencode_session": session}})
            raise

        chosen = report.choose(messages, require_reddit=not no_reddit)
        if server_error is not None and not messages:
            outcome, reason = "no_result", "server_failed"
        elif bounded:
            outcome, reason = "no_result", bounded
        elif chosen.text is not None:
            outcome, reason = "passed", None
        elif chosen.reason in report.CHECK_REASONS:
            outcome, reason = "failed", chosen.reason
        else:
            outcome, reason = "no_result", chosen.reason
        report_path = partial_path = None
        if outcome == "passed":
            _write(target, chosen.text)
            report_path = str(target)
        else:
            written = chosen.text or _all_text(messages)
            if written.strip():
                partial = target.with_name(f"{target.stem}.partial.md")
                _write(partial, written)
                partial_path = str(partial)
        result = Result(run, outcome, reason, alias, model_id, report_path, partial_path, chosen.report_from,
                        chosen.chars, chosen.links, chosen.reddit_urls, chosen.last_chars, chosen.compactions,
                        _minutes(started_mono, started_wall, wall), session, tree.detail())
        writer.write({"kind": "step.ended", **envelope, "detail": {
            "outcome": outcome, "reason": reason, "report_path": report_path, "partial_path": partial_path,
            "report_from": chosen.report_from, "report_chars": chosen.chars, "links": chosen.links,
            "reddit_urls": chosen.reddit_urls, "last_chars": chosen.last_chars,
            "compactions": chosen.compactions, "minutes": result.minutes, "opencode_session": session,
            "server_error": server_error.code if server_error else None, **tree.detail()}})
    return result


def _wait(client, session: str, settings: ResearchSettings, writer, envelope: dict, *, poll_seconds: float,
          progress_seconds: float, grace_seconds: float, wall) -> str | None:
    """None when the session went idle; `time_bound` or `stalled` when a bound ended it."""

    started_mono = time.monotonic()
    started_wall = quiet_from = wall()
    bound = settings.minutes * 60
    stall = settings.stall_minutes * 60
    seen_busy = False
    last_read = None
    while True:
        now_mono = time.monotonic()
        if now_mono - started_mono >= bound or wall() - started_wall >= bound:
            return "time_bound"
        busy = client.busy(session)
        seen_busy = seen_busy or busy
        if not busy and (seen_busy or now_mono - started_mono >= grace_seconds):
            return None
        if last_read is None or now_mono - last_read >= progress_seconds or wall() - quiet_from >= stall:
            last_read = now_mono
            progress = _progress(client, session)
            if progress is not None:
                if progress["newest_ms"] is not None:
                    quiet_from = max(quiet_from, progress["newest_ms"] / 1000)
                writer.write({"kind": "step.progressed", **envelope, "detail": {
                    "steps": progress["steps"], "sources": progress["sources"], "reddit_reached": progress["reddit"],
                    "helper_sessions": progress["helpers"], "quiet_seconds": round(max(wall() - quiet_from, 0), 1)}})
        if wall() - quiet_from >= stall:
            return "stalled"
        left = min(bound - (time.monotonic() - started_mono), quiet_from + stall - wall())
        time.sleep(max(0.05, min(poll_seconds, left)))


def _progress(client, root: str) -> dict | None:
    """Steps, distinct fetched sources, whether Reddit was reached and the newest step time
    (epoch ms) over the session tree; None when the server could not answer this time."""

    steps = helpers = 0
    newest = None
    sources: set[str] = set()
    seen, queue = {root}, deque([root])
    try:
        while queue and len(seen) <= usage.MAX_SESSIONS:
            sid = queue.popleft()
            for message in client.messages(sid):
                info = message.get("info") if isinstance(message, dict) else None
                info = info if isinstance(info, dict) else {}
                times = info.get("time") if isinstance(info.get("time"), dict) else {}
                for part in message.get("parts") or [] if isinstance(message, dict) else []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "step-start":
                        steps += 1
                        stamp = times.get("created")
                        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
                            newest = stamp if newest is None else max(newest, stamp)
                    state = part.get("state") if isinstance(part.get("state"), dict) else {}
                    url = (state.get("input") or {}).get("url") if isinstance(state.get("input"), dict) else None
                    if part.get("tool") == "webfetch" and state.get("status") == "completed" and isinstance(url, str):
                        sources.add(url)
            for child in client.children(sid):
                if child not in seen:
                    seen.add(child)
                    helpers += 1
                    queue.append(child)
    except OpencodeError:
        return None
    return {"steps": steps, "sources": len(sources), "reddit": any(map(report.is_reddit, sources)),
            "helpers": helpers, "newest_ms": newest}


def _all_text(messages: list) -> str:
    """Every text the model wrote in the run, in order (the compaction summaries left out)."""

    texts = []
    for message in messages:
        info = message.get("info") if isinstance(message, dict) else None
        if not isinstance(info, dict) or info.get("role") != "assistant" or info.get("summary") is True:
            continue
        texts += [part["text"] for part in message.get("parts") or []
                  if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)]
    return "\n".join(texts)


def write_result(path: Path, result: Result) -> None:
    """The result shape as JSON, written whole (the MCP front reads it as the job's answer)."""

    _write(Path(path), json.dumps(result.as_dict(), ensure_ascii=False, indent=1) + "\n")


def _write(path: Path, text: str) -> None:
    """The file written whole: a reader never sees half of it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, "utf-8", newline="\n")
    os.replace(temporary, path)


def _minutes(started_mono: float, started_wall: float, wall) -> float:
    return round(max(time.monotonic() - started_mono, wall() - started_wall, 0.0) / 60, 2)
