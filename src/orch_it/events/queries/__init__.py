"""The named queries of the store (#393 decisions 8 and 9): `ticket_cost`, `session_cost`,
`limit_rate`, and the share of the weekly subscription limit a cost is.

Each returns a `QueryResult` (columns and rows) for the CLI to print. The SQL of `ticket_cost`
is the event list's section 7, kept as a file beside this module and run as written.
"""

from __future__ import annotations

from datetime import datetime
from importlib import resources

from orch_it import settings
from orch_it.events import index
from orch_it.events.store import QueryResult, Store

WEEKLY_LIMIT_SETTING = "weekly_limit_usd_per_pct"
CLAUDE_PREFIX = "claude-"


def load_sql(name: str) -> str:
    return resources.files(__package__).joinpath(f"{name}.sql").read_text("utf-8")


# --- the share of the weekly limit -----------------------------------------------------------

def pct_week(usd: float | None, model: str | None, rate: float | None) -> str:
    """A cost as a share of the weekly limit: `n/a` for a model that is not Claude, `not set`
    while the setting is unset, `price_missing` for a turn without a price, else `usd / rate`
    (dollars per 1 %) as `0.18 %`. Never a guessed number."""

    if not (model or "").startswith(CLAUDE_PREFIX):
        return "n/a"
    if rate is None:
        return "not set"
    if usd is None:
        return "price_missing"
    return f"{usd / rate:.2f} %"


def weekly_rate() -> dict | None:
    """The setting `weekly_limit_usd_per_pct` as `{"usd_per_pct", "plan", "measured"}`, or None
    when unset. A setting of another shape raises `SettingsError("bad_setting")`."""

    value = settings.load().get(WEEKLY_LIMIT_SETTING)
    if value is None:
        return None
    rate = value.get("usd_per_pct") if isinstance(value, dict) else None
    if (not isinstance(rate, (int, float)) or isinstance(rate, bool) or not rate > 0
            or not isinstance(value.get("plan"), str) or not isinstance(value.get("measured"), str)):
        raise settings.SettingsError("bad_setting", WEEKLY_LIMIT_SETTING)
    return {"usd_per_pct": float(rate), "plan": value["plan"], "measured": value["measured"]}


def _usd_per_pct() -> float | None:
    rate = weekly_rate()
    return rate["usd_per_pct"] if rate else None


# --- ticket-cost -----------------------------------------------------------------------------

_SPEND_FILTER = "kind = 'turn.used' AND repo = :repo"
_CLAUDE_SPEND_FILTER = "kind = 'turn.used' AND model LIKE 'claude-%' AND repo = :repo"


def ticket_cost(store: Store, repo: str, n: int) -> QueryResult:
    """Section 7: the cost of ticket `n` from its ready edit to its close, per role and `how`
    (`direct`, or `shared` by the tickets a session held). `% week` is computed from the Claude
    part of each row: the same query restricted to `model LIKE 'claude-%'`."""

    sql = load_sql("ticket_cost")
    params = {"repo": repo, "n": n}
    claude_sql = sql.replace(_SPEND_FILTER, _CLAUDE_SPEND_FILTER)
    if claude_sql == sql:
        raise RuntimeError("ticket_cost.sql no longer holds the turn.used filter")
    rate = _usd_per_pct()
    claude = {(role, how): usd for role, how, usd in store.query(claude_sql, params).rows}
    rows = []
    for role, how, usd in store.query(sql, params).rows:
        part = (role, how) in claude
        rows.append((role, how, usd,
                     pct_week(claude.get((role, how)), CLAUDE_PREFIX if part else "", rate)))
    return QueryResult(["role", "how", "usd", "% week"], rows)


# --- session-cost ------------------------------------------------------------------------------

SESSION_COLUMNS = ["session", "machine", "model", "turns", "input", "output", "cache_read",
                   "cache_write", "usd", "% week", "minutes"]
_AT_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_SESSION_STEPS = "('session', 'subagent')"
_NO_TURNS = ("", 0, 0, 0, 0, 0, 0.0, 0)


class _Sums:
    """What a row or a total line adds up. `usd` leaves out the turns without a price."""

    FIELDS = ("turns", "input", "output", "cache_read", "cache_write", "usd", "missing",
              "claude_turns", "claude_missing", "claude_usd", "minutes")

    def __init__(self):
        for name in self.FIELDS:
            setattr(self, name, 0)

    def add_turns(self, model, turns, input_, output, cache_read, cache_write, usd, missing):
        self.turns += turns
        self.input += input_
        self.output += output
        self.cache_read += cache_read
        self.cache_write += cache_write
        self.usd += usd
        self.missing += missing
        if model.startswith(CLAUDE_PREFIX):
            self.claude_turns += turns
            self.claude_missing += missing
            self.claude_usd += usd

    def add_all(self, other: _Sums):
        for name in self.FIELDS:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def usd_cell(self, *, bare_when_all_missing: bool) -> str:
        if bare_when_all_missing and self.missing and self.missing == self.turns:
            return "price_missing"
        text = f"{self.usd:.2f}"
        return f"{text} ({self.missing} price_missing left out)" if self.missing else text

    def pct_cell(self, rate) -> str:
        if not self.claude_turns:
            return "n/a"
        priced = self.claude_turns > self.claude_missing
        return pct_week(self.claude_usd if priced else None, CLAUDE_PREFIX, rate)

    def row(self, label, machine, model, rate, minutes, *, bare_when_all_missing) -> tuple:
        return (label, machine, model, self.turns, self.input, self.output, self.cache_read,
                self.cache_write, self.usd_cell(bare_when_all_missing=bare_when_all_missing),
                self.pct_cell(rate), minutes)


def _utc(at: str) -> str:
    """`at` (ISO 8601 with a zone, or a bare date meaning UTC midnight) as the index writes it."""

    return index.utc_at(f"{at}T00:00:00Z" if len(at) == 10 else at)


def _minutes(first, last, started, ended) -> float:
    start, end = (started, ended or last) if started else (first, last)
    spent = datetime.strptime(end, _AT_FORMAT) - datetime.strptime(start, _AT_FORMAT)
    return max(spent.total_seconds() / 60, 0.0)


def session_cost(store: Store, *, session: str | None = None, repo: str | None = None,
                 since: str | None = None, until: str | None = None) -> QueryResult:
    """Dollars, `% week`, tokens and minutes per session and model, a subagent listed under its
    parent, a total line per parent (its subagents included) and a grand total.

    `session` keeps that session and its subagents; `repo`, `since` and `until` (inclusive)
    keep the events they match, so sums and minutes are of those events. A turn without a
    price shows `price_missing` and is left out of the sums. Minutes run from the session's
    `step.started` to its `step.ended`; with no end, to its last event; with no start, from its
    first event. A total line adds the minutes of the sessions it holds."""

    rate = _usd_per_pct()
    where, params = ["session IS NOT NULL"], {}
    for clause, name, value in (("repo = :repo", "repo", repo),
                                ("at >= :since", "since", since and _utc(since)),
                                ("at <= :until", "until", until and _utc(until))):
        if value is not None:
            where.append(clause)
            params[name] = value
    filters = " AND ".join(where)

    info = {}  # session -> (parent, machine, first at, last at, started at, ended at)
    for sid, *rest in store.query(
            "SELECT session, max(parent_session), min(machine), min(at), max(at),"
            f" min(CASE WHEN kind = 'step.started' AND step IN {_SESSION_STEPS} THEN at END),"
            f" max(CASE WHEN kind = 'step.ended' AND step IN {_SESSION_STEPS} THEN at END)"
            f" FROM events WHERE {filters} GROUP BY session", params).rows:
        info[sid] = rest

    def ancestors(sid):
        found, parent = [], info[sid][0]
        while parent is not None and parent != sid and parent not in found:
            found.append(parent)
            parent = info[parent][0] if parent in info else None
        return found

    models = {}  # session -> [(model, turns, input, output, cache_read, cache_write, usd, missing)]
    for sid, *rest in store.query(
            "SELECT session, coalesce(model, ''), count(*), coalesce(sum(input_tokens), 0),"
            " coalesce(sum(output_tokens), 0), coalesce(sum(cache_read_tokens), 0),"
            " coalesce(sum(cache_write_tokens), 0), coalesce(sum(usd), 0), sum(usd IS NULL)"
            f" FROM events WHERE kind = 'turn.used' AND {filters}"
            " GROUP BY session, model ORDER BY model", params).rows:
        models.setdefault(sid, []).append(tuple(rest))

    groups = {}  # the top-level session -> the sessions under it, itself first
    for sid in sorted(info, key=lambda s: (info[s][2], s)):
        chain = ancestors(sid)
        if session is None or session == sid or session in chain:
            groups.setdefault((chain or [sid])[-1], []).append(sid)

    rows, grand = [], _Sums()
    for root in sorted(groups, key=lambda r: (min(info[s][2] for s in groups[r]), r)):
        total = _Sums()
        for sid in sorted(groups[root], key=lambda s: (s != root, info[s][2], s)):
            _, machine, first, last, started, ended = info[sid]
            minutes = _minutes(first, last, started, ended)
            total.minutes += minutes
            label = sid if sid == root else f"  {sid}"
            for n, (model, *numbers) in enumerate(models.get(sid) or [_NO_TURNS]):
                sums = _Sums()
                sums.add_turns(model, *numbers)
                total.add_turns(model, *numbers)
                rows.append(sums.row(label, machine, model, rate, f"{minutes:.1f}" if n == 0 else "",
                                     bare_when_all_missing=True))
        rows.append(total.row(f"total {root}", "", "", rate, f"{total.minutes:.1f}",
                              bare_when_all_missing=False))
        grand.add_all(total)
    if groups:
        rows.append(grand.row("total all", "", "", rate, f"{grand.minutes:.1f}",
                              bare_when_all_missing=False))
    return QueryResult(list(SESSION_COLUMNS), rows)


# --- limit-rate --------------------------------------------------------------------------------

def limit_rate(store: Store) -> QueryResult:
    """Dollars at API prices per 1 % of the weekly limit, from each two consecutive readings
    (`value.measured`, `name` `weekly_limit_pct`): the `usd` of Claude `turn.used` events with
    `at` in (from, to] over the rise. A pair whose percent did not rise (the weekly reset)
    shows `reset`; one with a Claude turn without a price shows `price_missing`."""

    readings = store.query(
        "SELECT at, value FROM events WHERE kind = 'value.measured' AND name = 'weekly_limit_pct'"
        " AND typeof(value) IN ('integer', 'real') ORDER BY at, id").rows
    rows = []
    for (start, before), (end, after) in zip(readings, readings[1:]):
        usd, missing = store.query(
            "SELECT coalesce(sum(usd), 0), coalesce(sum(usd IS NULL), 0) FROM events"
            " WHERE kind = 'turn.used' AND model LIKE 'claude-%' AND at > ? AND at <= ?",
            (start, end)).rows[0]
        if after <= before:
            rate = "reset"
        elif missing:
            rate = "price_missing"
        else:
            rate = f"{usd / (after - before):.2f}"
        rows.append((start, end, before, after, round(usd, 2), rate))
    return QueryResult(["from", "to", "pct_before", "pct_after", "usd", "usd_per_pct"], rows)
