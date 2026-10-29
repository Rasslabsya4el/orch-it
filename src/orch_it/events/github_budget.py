"""The request budget of the layer (#414 decision 9): every GitHub call is counted per account per hour in the
store, and the Actions-token budget is stored per repo.

Two budgets, two tokens:

- the account's budget (GitHub's core limit per user token): each call the layer makes with `gh` is one
  `github.called` event with `budget` `account`, its `account` the login of the token the layer uses (one
  `gh api user --jq .login` per process; the owner of the repo the call names when that call fails). GitHub's
  window is not the clock hour: a `gh api rate_limit` answer carries the `reset` of the window, `run_gh` stores it
  as a reading (`budget_reset` set, not a counted call), and `hourly_totals` counts the calls inside the window
  that ends at the latest remembered reset (reset - 1 h .. reset), the UTC clock hours when no reset is known;
- the Actions token's budget per repo (about 1000 requests an hour): a reading `budget` `actions` with the repo's
  remaining, limit and reset, stored with the repo (the importer writes one per run from the run's
  `actions-rate-limit.json`). `would_cross` asks whether spending some more calls takes the repo under its
  threshold, the place rule's question; `actions_spend` is what the readings of the latest window show was spent.

Pollers read the remaining limit GitHub returns (`GET /rate_limit`) and wait longer through `poll_interval` when the
remaining falls under the threshold, a share of the limit (setting `request_budget_threshold`, default 20%).

Known gap: the event list needs a repo on every event, so a call that names no repo (`gh api user`,
`users/<login>`) is made and counted by nobody here: `count_call` takes no repo and stores nothing. A `rate_limit`
reading takes the repo of the last call this process counted (before any, it is not stored); GitHub does not count
`rate_limit` calls against the limit, nor does the counter. The window of the latest reset is not rolled forward
once the clock passes it.
Counting never fails a call: every failure of the store is swallowed by `count_call`.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
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
RESET_WINDOW = timedelta(hours=1)  # GitHub's window ends at its `reset` and began an hour before
ACTIONS_FILE = "actions-rate-limit.json"  # the run's own `gh api rate_limit`, written by the workflow (#414 unit 10)
LOGIN_TIMEOUT_S = 20
LOGIN_RETRY_S = 60  # after a failed `gh api user` the next count within this many seconds does not ask again
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
_AT_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"  # the index's own `at` format: text order is time order
_PATH_REPO =re.compile(r"repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/?]|$)")
_OWNER_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REPO_FLAGS = ("--repo", "-R")
dropped_calls = 0  # calls whose count the store refused (counting never fails a call); this process's count
_login: str | None = None  # the token's login, read once per process
_login_failed_at: float | None = None
_last_repo: str | None = None  # the repo of the last call counted: a `rate_limit` reading names none of its own


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

    global _last_repo
    if repo is None:
        return
    _last_repo = repo
    try:
        _write(store or Store.open(), reading_event(repo, budget="account", remaining=None, limit=None, at=at))
    except Exception as failure:  # counting never fails the call it counts
        _dropped(failure)


def after_call(args: list[str], answer: str | None) -> None:
    """What `run_gh` does once a `gh` call ended: a `rate_limit` read (`answer` its text, None when it failed) is
    remembered as a window reading, any other call is counted. Never raises."""

    if any(arg in ("rate_limit", "/rate_limit") for arg in args):
        if answer is not None:
            note_rate_limit(answer)
    else:
        count_call(repo_of(args))


def note_rate_limit(text: str, *, store: Store | None = None, repo: str | None = None,
                    at: datetime | None = None) -> None:
    """Remember the account reading in a `gh api rate_limit` answer (its `resources.core` remaining, limit and
    reset) as an event of `repo` (default: the last repo this process counted). An answer that is no reading, or
    carries no reset, stores nothing. Never raises."""

    repo = repo or _last_repo
    reading = read_core(text)
    if repo is None or reading is None or reading[2] is None:
        return
    try:
        _write(store or Store.open(), reading_event(repo, budget="account", remaining=reading[0], limit=reading[1],
                                                     reset=reading[2], at=at))
    except Exception as failure:
        _dropped(failure)


def _dropped(failure: Exception) -> None:
    global dropped_calls
    dropped_calls += 1
    print(f"orch-it: a GitHub call was not counted ({type(failure).__name__}: {failure})", file=sys.stderr)


def read_core(text: str) -> tuple[int, int, datetime | None] | None:
    """(remaining, limit, reset) of `resources.core` in the raw JSON of `gh api rate_limit`; None when the text is no
    such answer. A reset that is no epoch time is None, the reading stays."""

    try:
        core = json.loads(text)["resources"]["core"]
        remaining, limit, reset = core["remaining"], core["limit"], core.get("reset")
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if type(remaining) is not int or type(limit) is not int or remaining < 0 or limit <= 0:
        return None
    moment = None
    if type(reset) is int and reset > 0:
        try:
            moment = datetime.fromtimestamp(reset, UTC)
        except (OverflowError, OSError, ValueError):
            moment = None
    return remaining, limit, moment


def record_budget(store: Store, repo: str, *, budget: str, remaining: int, limit: int,
                  reset: datetime | None = None, at: datetime | None = None) -> None:
    """Store a budget reading of `repo`: its remaining calls and its limit, as GitHub reports them, and the `reset`
    of the window (`budget` `actions` for the Actions token's budget of the repo). A bad value raises the store's
    own error."""

    _write(store, reading_event(repo, budget=budget, remaining=remaining, limit=limit, reset=reset, at=at))


def reading_event(repo: str, *, budget: str, remaining: int | None, limit: int | None, reset: datetime | None = None,
                  at: datetime | None = None, event_id: str | None = None) -> dict:
    """The `github.called` event of a counted call (`remaining`, `limit`, `reset` None) or of a budget reading."""

    event = {
        "kind": KIND, "repo": repo, "ticket": None, "role": ROLE, "session": None, "parent_session": None,
        "turn": None, "step": None, "run": None, "note": None,
        "detail": {"budget": budget, "account": account_key(repo), "budget_remaining": remaining,
                   "budget_limit": limit,
                   "budget_reset": reset.astimezone(UTC).strftime(_AT_FORMAT) if reset else None},
    }
    if at is not None:
        event["at"] = at.isoformat()
    if event_id is not None:
        event["id"] = event_id
    return event


def account_login() -> str | None:
    """The login of the token the layer uses, lowercase: `gh api user --jq .login` once per process. None when the
    call fails; the next count within `LOGIN_RETRY_S` does not ask again."""

    global _login, _login_failed_at
    if _login is not None:
        return _login
    if _login_failed_at is not None and time.monotonic() - _login_failed_at < LOGIN_RETRY_S:
        return None
    found = _read_login()
    if found is None:
        _login_failed_at = time.monotonic()
        return None
    _login = found
    return found


def _read_login() -> str | None:
    from orch_it.testruns import github  # it imports this module

    try:
        text = github.call_gh(["api", "user", "--jq", ".login"], timeout=LOGIN_TIMEOUT_S).strip()
    except github.GithubError:
        return None
    return text.lower() if _LOGIN.fullmatch(text) else None


def account_key(repo: str) -> str:
    """The account a call about `repo` is counted against: the token's login, else the repo's owner."""

    return account_login() or _account_key(repo)


def latest_reset(store: Store, account: str, *, budget: str = "account") -> datetime | None:
    """The latest `reset` of a window remembered for `account`, None when no reading carried one."""

    ((found,),) = store.query(
        f"SELECT MAX(budget_reset) FROM events WHERE kind = '{KIND}' AND budget = ? AND account = ?",
        (budget, account)).rows
    return schema.parse_at(found) if found else None


def hourly_totals(store: Store, account: str | None = None, *, budget: str = "account") -> dict[str, int]:
    """The calls counted for `account` (default: the token's login) inside GitHub's window that ends at the latest
    remembered reset (reset - 1 h up to the reset), as `{window start 'YYYY-MM-DDTHH:MM': calls}`. With no reset
    known, per UTC clock hour, keyed `YYYY-MM-DDTHH`."""

    if account is None:
        account = account_login()
        if account is None:
            raise ValueError("the token's login is not known: name the account")
    key = _account_key(account)
    where = f"kind = '{KIND}' AND budget = ? AND account = ? AND budget_reset IS NULL"
    reset = latest_reset(store, key, budget=budget)
    if reset is None:
        rows = store.query(f"SELECT substr(at, 1, 13) AS hour, COUNT(*) FROM events WHERE {where}"
                           " GROUP BY hour ORDER BY hour", (budget, key)).rows
        return {hour: count for hour, count in rows}
    start = reset - RESET_WINDOW
    ((count,),) = store.query(f"SELECT COUNT(*) FROM events WHERE {where} AND at >= ? AND at < ?",
                              (budget, key, start.strftime(_AT_FORMAT), reset.strftime(_AT_FORMAT))).rows
    return {start.strftime("%Y-%m-%dT%H:%M"): count}


def actions_spend(repo: str, *, store: Store | None = None) -> int:
    """How many Actions requests `repo` spent in its latest window, from the stored readings: the remaining at the
    window's first reading less the remaining at its last. The window ends at the latest remembered reset (with no
    reset known, at the latest reading). 0 with fewer than two readings."""

    store = store or Store.open()
    ((reset,),) = store.query(f"SELECT MAX(budget_reset) FROM events WHERE kind = '{KIND}' AND budget = 'actions'"
                              " AND repo = ?", (repo,)).rows
    if reset:
        end = schema.parse_at(reset)
    else:
        ((latest,),) = store.query(f"SELECT MAX(at) FROM events WHERE kind = '{KIND}' AND budget = 'actions'"
                                   " AND repo = ?", (repo,)).rows
        if not latest:
            return 0
        end = schema.parse_at(latest) + timedelta(microseconds=1)
    rows = store.query(
        f"SELECT budget_remaining FROM events WHERE kind = '{KIND}' AND budget = 'actions' AND repo = ?"
        " AND budget_remaining IS NOT NULL AND at >= ? AND at < ? ORDER BY at",
        (repo, (end - RESET_WINDOW).astimezone(UTC).strftime(_AT_FORMAT),
         end.astimezone(UTC).strftime(_AT_FORMAT))).rows
    return max(rows[0][0] - rows[-1][0], 0) if len(rows) >= 2 else 0


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


def _write(store: Store, event: dict) -> None:
    with store.writer() as writer:
        writer.write(event)
