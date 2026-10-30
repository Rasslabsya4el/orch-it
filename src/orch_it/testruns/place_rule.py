"""The place rule (#394 decision 5): which test place one run goes to.

`choose` is pure. Its inputs: the OS the run needs (`linux`, `windows`, `macos` or `any`), the
places in tie order (own, machines in settings order, github), the live runs per place and the
measured durations per place, both read from the store by `read_facts`, so a decision replays
from the store alone and reads the same on every OS (load is a count of live runs, never the
OS's CPU load). In order:

1. drop a place without that OS (`os_missing`), switched off (`place_off`), a private repo's GitHub over its
   month's minutes (`out_of_minutes`: #406 decision 4, `Offer.budget`: the minutes used plus the median billed minutes
   of the part's ended GitHub runs, else of all of them, else 0, pass the allowance), at capacity (`busy`), or lost
   earlier in this same run (`excluded`, e.g. `place_vanished`);
2. for each place left, expected finish = start overhead (median of its measured starts, 0 with
   none) + run time (median of its measured runs). A place with no measured run takes the own
   place's median; when own has none either, the median of every place's measured runs;
3. with nothing measured anywhere the run goes to own, or, when own is dropped, to the first
   place left (`nothing_measured`); else to the earliest expected finish (`earliest_finish`),
   a tie going to the earlier place in tie order. A metered place (a private repo's GitHub: its minutes cost
   money) is a candidate only when no free place is left; it is still offered, with its expected finish;
4. no place left: no choice (the caller writes a `wait` with reason `test_place`).
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from orch_it.events import index
from orch_it.events.store import Store, machine_id
from orch_it.testruns.config import DEFAULT_MINUTE_MULTIPLIERS
from orch_it.testruns.github_minutes import charged

TIE_DIGITS = 9  # finishes equal to this many decimals of a minute are a tie
MEASURED_RUNS = 10  # decision 5: the median of the last 10 ended passed / failed runs
LIVE_GRACE_MINUTES = 10  # a started run with no end is live for its bound plus this
DEFAULT_BOUND_MINUTES = 15.0
MAX_MINUTES = 7 * 24 * 60  # a bound or a measurement over a week is no reading
# The drop reasons, each a code of `test_run_reason`, in the order the rule checks them; a place
# left out after it refused or vanished in this run keeps that attempt's own code.
OS_MISSING, PLACE_OFF, OUT_OF_MINUTES, BUSY = "os_missing", "place_off", "out_of_minutes", "busy"
DROP_CODES = (OS_MISSING, PLACE_OFF, OUT_OF_MINUTES, BUSY)
EARLIEST, NOTHING_MEASURED = "earliest_finish", "nothing_measured"


@dataclass(frozen=True)
class Budget:
    """A private repo's GitHub minutes of the UTC month at a decision: `used` of `allowance`, and the day (`resets`, the 1st
    of the next month) they start again at 0."""

    used: float
    allowance: int
    resets: date


@dataclass(frozen=True)
class Offer:
    place: str
    oses: tuple[str, ...]
    on: bool
    capacity: int
    metered: bool = False  # a run there costs money: it is chosen only when no free place is left
    budget: Budget | None = None  # the month's minutes of a metered place; None: a public repo's, never refused on minutes


@dataclass(frozen=True)
class Measured:
    starts: tuple[float, ...] = ()  # start overheads in minutes
    runs: tuple[float, ...] = ()  # run times in minutes (the last ended passed/failed runs)
    # GitHub minutes the ended runs counted (`github_minutes.charged`, the OS multiplier in): the part's own, and every
    # run's at the place, the last `MEASURED_RUNS` of each; only the `github` place has them
    billed: tuple[float, ...] = ()
    billed_all: tuple[float, ...] = ()


@dataclass(frozen=True)
class Offered:
    place: str
    expected_minutes: float | None
    dropped: str | None
    note: str | None = None  # words beside the place: its busy count, or why it is off (`places.Availability`)


@dataclass(frozen=True)
class Decision:
    chosen: str | None
    reason: str | None
    offered: tuple[Offered, ...] = field(default_factory=tuple)

    def detail(self) -> list[dict]:
        """`places_offered` of the `test_run` start: each place with its expected finish in
        minutes (2 decimals) or its drop reason."""

        return [{"place": o.place,
                 "expected_minutes": None if o.expected_minutes is None else round(o.expected_minutes, 2),
                 "dropped": o.dropped} | ({"note": o.note} if o.note else {}) for o in self.offered]


def _over_budget(offer: Offer, measured: dict[str, Measured]) -> str | None:
    """Why a metered place is out of minutes: `used` plus the median billed minutes of the part's ended runs there (with
    none, of all its ended runs; with none at all, 0) passes the allowance; None when it does not or the offer has no
    budget. The words name used, median, allowance and the day the minutes start again (`dd.mm`)."""

    budget = offer.budget
    if budget is None:
        return None
    place = measured.get(offer.place, Measured())
    sample = place.billed or place.billed_all
    median = statistics.median(sample) if sample else 0
    if budget.used + median <= budget.allowance:
        return None
    return (f"GitHub minutes used {budget.used:g} + median run {median:g} pass the allowance {budget.allowance:g}"
            f" until {budget.resets:%d.%m}")


def _drop(offer: Offer, need_os: str, live: dict[str, int], excluded: dict[str, str],
          measured: dict[str, Measured] | None = None) -> str | None:
    if need_os != "any" and need_os not in offer.oses:
        return OS_MISSING
    if not offer.on:
        return PLACE_OFF
    if _over_budget(offer, measured or {}) is not None:
        return OUT_OF_MINUTES
    if live.get(offer.place, 0) >= offer.capacity:
        return BUSY
    return excluded.get(offer.place)


def choose(need_os: str, places: list[Offer], live: dict[str, int],
           measured: dict[str, Measured], excluded: dict[str, str] | None = None,
           notes: dict[str, str] | None = None) -> Decision:
    """`notes`: a place's words for the decision (its busy count), kept beside it in `places_offered`."""

    excluded = excluded or {}
    notes = notes or {}
    drops = {o.place: _drop(o, need_os, live, excluded, measured) for o in places}
    notes = notes | {o.place: _over_budget(o, measured) for o in places if drops[o.place] == OUT_OF_MINUTES}
    left = [o for o in places if drops[o.place] is None]
    candidates = [o for o in left if not o.metered] or left
    every_run = [m for place in measured.values() for m in place.runs]
    if not every_run:
        offered = tuple(Offered(o.place, None, drops[o.place], notes.get(o.place)) for o in places)
        if not left:
            return Decision(None, None, offered)
        pick = next((o for o in candidates if o.place == "own"), candidates[0])
        return Decision(pick.place, NOTHING_MEASURED, offered)
    own_runs = measured.get("own", Measured()).runs
    reference = statistics.median(own_runs or every_run)
    finish = {}
    for o in left:
        m = measured.get(o.place, Measured())
        start = statistics.median(m.starts) if m.starts else 0.0
        finish[o.place] = start + (statistics.median(m.runs) if m.runs else reference)
    offered = tuple(Offered(o.place, finish.get(o.place), drops[o.place], notes.get(o.place)) for o in places)
    if not left:
        return Decision(None, None, offered)
    best = min(candidates, key=lambda o: round(finish[o.place], TIE_DIGITS))  # min keeps the first of a tie
    return Decision(best.place, EARLIEST, offered)


@dataclass(frozen=True)
class _Started:
    """A `test_run` start as the rule reads it; `part` is the job's name of a GitHub run (#406 decision 1), else None."""

    place: str
    repo: str
    selection: str | None
    run_os: str | None
    at: str
    machine: str
    bound: float
    part: str | None = None


def _of_part(start: _Started, selection: str, part: str | None) -> bool:
    """Whether a started run is the part measured: a GitHub run that names its job is the part of that name, when `part`
    names one; any other run is of the selection (the same run's selection, the rule's own and machine runs alike)."""

    if part is not None and start.place == "github" and start.part is not None:
        return start.part == part
    return start.selection == selection


def read_facts(store: Store, *, repo: str, selection: str, need_os: str, now: datetime,
               machine: str | None = None, part: str | None = None,
               multipliers: dict[str, int] = DEFAULT_MINUTE_MULTIPLIERS,
               ) -> tuple[dict[str, int], dict[str, Measured]]:
    """The rule's store inputs at `now`: live runs per place (of every repo: a machine's capacity
    is shared), and per place the measured run times (the last `MEASURED_RUNS` ended `passed` /
    `failed` runs of `repo` and `selection`, on `need_os` unless it is `any`) and start
    overheads (the last `MEASURED_RUNS` ended runs at the place with one measured, any repo).

    `part`: the job whose GitHub runs are measured, by the job's name (a GitHub run started with its `part`
    counts only for that name; without one, every GitHub run of the selection counts). `multipliers`: the
    OS factors the billed minutes are charged at (the setting `github_minute_multipliers`).

    `own` is this machine (`machine`, default the store's machine id): another machine's own
    runs, in a store merged by copying, are neither live nor measured here. A start dated more
    than the grace ahead of `now` (a skewed clock) is no live run; a bound or a measurement
    that is no number within a week is no reading. The live count is read, not reserved: two
    runs deciding at the same moment may both take a place's last free slot."""

    machine = machine or machine_id()
    started: dict[str, _Started] = {}
    for run, place, run_repo, run_selection, run_os, at, run_machine, detail in store.query(
            "SELECT run, place, repo, selection, run_os, at, machine, detail FROM events"
            " WHERE kind = 'step.started' AND step = 'test_run' AND run IS NOT NULL").rows:
        if not isinstance(place, str) or (place == "own" and run_machine != machine):
            continue
        fields = json.loads(detail)
        bound = _minutes(fields.get("bound_minutes")) or DEFAULT_BOUND_MINUTES
        job = fields.get("part") if isinstance(fields.get("part"), str) else None
        started.setdefault(run, _Started(place, run_repo, run_selection, run_os, at, run_machine,
                                         bound, job))
    ends = store.query(
        "SELECT run, outcome, run_minutes, start_minutes, billed_minutes FROM events"
        " WHERE kind = 'step.ended' AND step = 'test_run' AND run IS NOT NULL ORDER BY at DESC, id").rows

    ended_runs = {run for run, *_ in ends}
    latest = _utc(now + timedelta(minutes=LIVE_GRACE_MINUTES))
    live: dict[str, int] = {}
    for run, start in started.items():
        earliest = _utc(now - timedelta(minutes=start.bound + LIVE_GRACE_MINUTES))
        if run not in ended_runs and earliest <= start.at <= latest:
            live[start.place] = live.get(start.place, 0) + 1

    runs: dict[str, list[float]] = {}
    starts: dict[str, list[float]] = {}
    billed: dict[str, list[float]] = {}
    billed_all: dict[str, list[float]] = {}
    for run, outcome, run_minutes, start_minutes, billed_minutes in ends:
        start = started.get(run)
        if start is None or (need_os != "any" and start.run_os != need_os):
            continue
        place = start.place
        same_part = start.repo == repo and _of_part(start, selection, part)
        if _minutes(start_minutes) is not None and len(starts.setdefault(place, [])) < MEASURED_RUNS:
            starts[place].append(_minutes(start_minutes))
        if (outcome in ("passed", "failed") and same_part
                and _minutes(run_minutes) is not None
                and len(runs.setdefault(place, [])) < MEASURED_RUNS):
            runs[place].append(_minutes(run_minutes))
        if place == "github" and outcome in ("passed", "failed") and _minutes(billed_minutes) is not None:
            cost = charged(_minutes(billed_minutes), start.run_os, multipliers)
            if len(billed_all.setdefault(place, [])) < MEASURED_RUNS:
                billed_all[place].append(cost)
            if same_part and len(billed.setdefault(place, [])) < MEASURED_RUNS:
                billed[place].append(cost)
    measured = {place: Measured(tuple(starts.get(place, ())), tuple(runs.get(place, ())),
                                tuple(billed.get(place, ())), tuple(billed_all.get(place, ())))
                for place in set(starts) | set(runs) | set(billed_all)
                if starts.get(place) or runs.get(place) or billed_all.get(place)}
    return live, measured


def _minutes(value: object) -> float | None:
    """A number of minutes from the store: finite, 0 to `MAX_MINUTES`; else None."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        minutes = float(value)
    except OverflowError:
        return None
    return minutes if math.isfinite(minutes) and 0 <= minutes <= MAX_MINUTES else None


def _utc(moment: datetime) -> str:
    return index.utc_at(moment.isoformat())
