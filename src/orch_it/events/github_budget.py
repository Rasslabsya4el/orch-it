"""The request budget of the layer (#414 decision 9): every GitHub call is counted per account per hour in the
store, and the Actions-token budget is stored per repo.

Two budgets, two tokens:

- the account's budget (GitHub's core limit per user token): each call the layer makes with `gh` is one
  `github.called` event with `budget` `account`, its `account` the owner of the repo the call names. `hourly_totals`
  reads the counts back, grouped by the UTC hour;
- the Actions token's budget per repo (about 1000 requests an hour): a reading `budget` `actions` with the repo's
  remaining and limit, stored with the repo. `would_cross` asks whether spending some more calls takes the repo under
  its threshold, the place rule's question.

Pollers read the remaining limit GitHub returns (`GET /rate_limit`) and wait longer through `poll_interval` when the
remaining falls under the threshold, a share of the limit (setting `request_budget_threshold`, default 20%).

Known gap: the event list needs a repo on every event, so a call that names no repo (`gh api user`,
`users/<login>`, `rate_limit`) is made and counted by nobody here: `count_call` takes no repo and stores nothing.
Counting never fails a call: every failure of the store is swallowed by `count_call`.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import UTC, datetime, timedelta
from importlib import resources

from orch_it import settings
from orch_it.events import schema
from orch_it.events.store import Store

KIND = "github.called"
ROLE = "script"  # the layer's own code, not a role's session: a call's role is not known where it is made
THRESHOLD_KEY = "request_budget_threshold"  # a share of the limit: a poller slows under it; its default is in the json
DEFAULTS_FILE = "github_budget_defaults.json"
MAX_POLL_SECONDS = 3600  # the longest a poller waits, however far under the threshold
ACTIONS_WINDOW = timedelta(hours=1)  # an Actions reading older than this says nothing about the budget now
_PATH_REPO = re.compile(r"repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/?]|$)")
_OWNER_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REPO_FLAGS = ("--repo", "-R")
dropped_calls = 0  # calls whose count the store refused (counting never fails a call); this process's count


def default_threshold() -> float:
    """The default share of the limit, read from `github_budget_defaults.json` beside this module."""

    text = resources.files("orch_it.events").joinpath(DEFAULTS_FILE).read_text("utf-8")
    return float(json.loads(text)[THRESHOLD_KEY])


def threshold(raw: dict | None = None) -> float:
    """The share of the limit under which a poller slows: the setting `request_budget_threshold` read with
    `dict.get`, the default from the json when absent. A value that is not a number from 0 to 1 raises `bad_setting`."""

    raw = settings.load() if raw is None else raw
    value = raw.get(THRESHOLD_KEY, default_threshold())
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise settings.SettingsError("bad_setting", f"{THRESHOLD_KEY}: a share from 0 to 1")
    return float(value)


def poll_interval(base_seconds: float, remaining: int | None, limit: int | None, threshold_share: float) -> float:
    """The wait before a poller's next read. `base_seconds` while `remaining` is at least `threshold_share` times
    `limit` (or nothing is known). Under it the interval doubles at each halving of the threshold level the remaining
    has fallen to: `1 + floor(log2(threshold_share * limit / remaining))` doublings, never past `MAX_POLL_SECONDS`
    (and never below `base_seconds`). An exhausted budget waits the cap."""

    if remaining is None or limit is None or limit <= 0:
        return float(base_seconds)
    level = threshold_share * limit
    if remaining >= level:
        return float(base_seconds)
    if remaining <= 0:
        return float(max(base_seconds, MAX_POLL_SECONDS))
    doublings = 1 + math.floor(math.log2(level / remaining))
    return float(max(base_seconds, min(base_seconds * 2 ** doublings, MAX_POLL_SECONDS)))


def repo_of(args: list[str]) -> str | None:
    """The `owner/name` a `gh` call names: its `--repo X`, `--repo=X` or `-R X` flag (`pr create`, `pr merge`,
    `run download`), else its `repos/<owner>/<name>` path, or the `owner=` and `name=` fields of a GraphQL call.
    None when it names no repo."""

    for i, arg in enumerate(args):
        if arg in _REPO_FLAGS and i + 1 < len(args):
            candidate = args[i + 1]
        elif arg.startswith("--repo="):
            candidate = arg[len("--repo="):]
        else:
            continue
        if _OWNER_REPO.fullmatch(candidate):
            return candidate
    for arg in args:
        found = _PATH_REPO.search(arg)
        if found:
            return f"{found.group(1)}/{found.group(2)}"
    owner = next((arg[len("owner="):] for arg in args if arg.startswith("owner=")), None)
    name = next((arg[len("name="):] for arg in args if arg.startswith("name=")), None)
    return f"{owner}/{name}" if owner and name else None


def count_call(repo: str | None, *, store: Store | None = None, at: datetime | None = None) -> None:
    """Count one GitHub call about `repo` in the store, against its account. A call with no repo is not stored. Never
    raises: a store that cannot be written loses the count, not the call; the loss is counted in `dropped_calls` and
    said in one line on stderr."""

    global dropped_calls
    if repo is None:
        return
    try:
        _write(store or Store.open(), repo, budget="account", remaining=None, limit=None, at=at)
    except Exception as failure:  # counting never fails the call it counts
        dropped_calls += 1
        print(f"orch-it: a GitHub call was not counted ({type(failure).__name__}: {failure})", file=sys.stderr)


def record_budget(store: Store, repo: str, *, budget: str, remaining: int, limit: int,
                  at: datetime | None = None) -> None:
    """Store a budget reading of `repo`: its remaining calls and its limit, as GitHub reports them (`budget`
    `actions` for the Actions token's budget of the repo). A bad value raises the store's own error."""

    _write(store, repo, budget, remaining, limit, at)


def hourly_totals(store: Store, account: str, *, budget: str = "account") -> dict[str, int]:
    """The calls counted for `account` per UTC hour, keyed `YYYY-MM-DDTHH`."""

    rows = store.query(
        f"SELECT substr(at, 1, 13) AS hour, COUNT(*) FROM events WHERE kind = '{KIND}' AND budget = ? AND account = ?"
        " GROUP BY hour ORDER BY hour", (budget, _account_key(account))).rows
    return {hour: count for hour, count in rows}


def would_cross(repo: str, calls: int, *, store: Store | None = None, now: datetime | None = None,
                raw: dict | None = None) -> bool:
    """Whether `calls` more Actions requests in `repo` would take its remaining budget under the threshold. The
    latest stored reading of the repo decides; no reading, or one older than `ACTIONS_WINDOW`, never crosses."""

    store = store or Store.open()
    now = now or datetime.now(UTC)
    rows = store.query(
        f"SELECT at, budget_remaining, budget_limit FROM events WHERE kind = '{KIND}' AND budget = 'actions'"
        " AND repo = ? ORDER BY at DESC LIMIT 1", (repo,)).rows
    if not rows:
        return False
    at, remaining, limit = rows[0]
    if remaining is None or not limit or now - schema.parse_at(at) > ACTIONS_WINDOW:
        return False
    return remaining - calls < threshold(raw) * limit


def _account_key(name: str) -> str:
    """The account a repo or an account name belongs to, lowercase: the part before `/`, as the store keeps it."""

    return name.split("/", 1)[0].lower()


def _write(store: Store, repo: str, budget: str, remaining: int | None, limit: int | None,
           at: datetime | None) -> None:
    event = {
        "kind": KIND, "repo": repo, "ticket": None, "role": ROLE, "session": None, "parent_session": None,
        "turn": None, "step": None, "run": None, "note": None,
        "detail": {"budget": budget, "account": _account_key(repo),
                   "budget_remaining": remaining, "budget_limit": limit},
    }
    if at is not None:
        event["at"] = at.isoformat()
    with store.writer() as writer:
        writer.write(event)
