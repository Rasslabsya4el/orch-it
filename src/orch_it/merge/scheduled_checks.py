"""The scheduled checks (#414 items 3 and 8): `run_scheduled_checks` is one tick, called by `reconcile` after the
workflows' events are in the store (`bring_workflow_events`), so the store holds the runs the checks read. A tick
runs, each on its own:

- `run_bound_check`: a `needs-triage` ticket per place, OS and kind whose median is past its bound, or a comment
  on the open one;
- `post_windows_time`: the per-change Windows time against its target, once per newest run;
- `run_unmarked_check`: a ticket per unmarked Windows-only failure of the newest nightly, then
- `post_windows_nightly_time`: the nightly's Windows time as a comment on the open time ticket.

Every check is idempotent by the marks its tickets carry, so a tick twice posts and opens nothing twice. A check that
raises is written as an `error.raised` (component `scheduled_checks`, under the tick's role) and named in `failed`;
the others still run, and a store that refuses that record does not stop them either.

Two sweeps run a tick, and they differ in what the store holds. On the Actions sweep (the workflow `reconcile`, the
store fresh, no test-run events) only the unmarked check works: it reads GitHub's reports of the nightly, not the
store. The bound check and the two time posts read the store's test-run events, so they take effect on the machine
sweep (`bring_events`, the store holds the runs).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orch_it.events.store import Store
from orch_it.merge import record
from orch_it.merge.hub import PullHub
from orch_it.testruns.bound_check import BoundReport, run_bound_check
from orch_it.testruns.windows_unmarked import (
    NightlyTime,
    UnmarkedReport,
    WindowsTime,
    post_windows_nightly_time,
    post_windows_time,
    run_unmarked_check,
)

COMPONENT = "scheduled_checks"
FAILURE_CHARS = 200


@dataclass(frozen=True)
class ScheduledChecks:
    bound: BoundReport | None = None  # None: the check raised
    windows_time: WindowsTime | None = None
    unmarked: UnmarkedReport | None = None
    nightly_time: NightlyTime | None = None
    failed: list[str] = field(default_factory=list)  # "<check>: <error>", one per check that raised


def run_scheduled_checks(store: Store, hub: PullHub, repo: str, raw: dict, run: str, role: str) -> ScheduledChecks:
    """One tick of the scheduled checks of `repo`; `raw` is the settings file's content, `run` the sweep's run id,
    `role` the role a failed check's `error.raised` is written under."""

    settings = {**raw, "repo": repo}
    checks = {
        "bound": lambda: run_bound_check(store, hub, settings),
        "windows_time": lambda: post_windows_time(store, hub, settings),
        "unmarked": lambda: run_unmarked_check(store, hub, settings),
        "nightly_time": lambda: post_windows_nightly_time(store, hub, settings),
    }
    done: dict[str, object] = {}
    failed: list[str] = []
    for name, check in checks.items():
        try:
            done[name] = check()
        except Exception as e:  # one check that breaks never stops the next one or the sweep (#414 item 8)
            failed.append(f"{name}: {e}"[:FAILURE_CHARS])
            try:
                record.write(store, repo, None, role, run, None, [("error.raised", {
                    "component": COMPONENT, "error_code": getattr(e, "code", type(e).__name__), "failed_open": False})])
            except Exception:  # a store that refuses the record still leaves the check named in `failed` (#414 item 3)
                pass
    return ScheduledChecks(bound=done.get("bound"), windows_time=done.get("windows_time"),
                           unmarked=done.get("unmarked"), nightly_time=done.get("nightly_time"), failed=failed)
