# A test run is read from its JUnit report, placed by measured finish, and gated by the full suite

A test run is one call of the repo's test command (the setting `test_command`, default pytest writing `--junitxml`)
at one test place on one commit, and its outcome is read only from the JUnit XML report it writes, never from its
exit code or its text output; the place rule sends each run where it is expected to finish first, counting load as
the live runs in the store; and the required checks `linux` and `windows` run the full suite on every pull request,
with no selection and no split into parts in this part (Rasslabsya4el/orchestration-layer#394, decisions 1, 5, 6).
JUnit XML because pytest, jest-junit, go-junit-report, gotestsum and Maven Surefire all write it, so a stranger's
repo in any language is read the same way; load from the store because it reads the same on Windows, Linux and macOS
and a decision replays from the store alone; the full suite because orch-it's suite runs in minutes and GitHub caps
concurrent jobs per account, so a split adds queue rather than removing it.

## Considered Options

- **Reading pytest's text output or exit codes**: binds the layer to one tool, and reads a stranger's JavaScript or
  Go repo as `summary_missing` or as green.
- **The OS's CPU load as the load measure**: reads differently per OS and cannot be replayed from the store.
- **Test selection or a split into parts for the required checks**: a selection map is a second source of truth to
  keep correct, and selection never decides a required check (TEST-3, TEST-4).

## Consequences

- **"Full suite" is every test but those marked `slow` and `live`**, which the nightly run (and a dispatch) adds
  (decision 8).
- **Every decision is in the store.** The `test_run` start holds `places_offered` (each place with its expected
  finish in minutes or its drop reason), `chosen` and `reason`; places are dropped for the OS, then for being
  switched off, then for capacity; a live run is a start with no end, younger than its bound plus a grace.
- **Slowness is answered by measurement.** `test-places` shows the median run and GitHub queue minutes per place and
  OS; when either passes the bound, a ticket is opened from those numbers. #378's "Windows the thin system module per
  change" is held to the same measurement: until the Windows full run nears its bound there is nothing to thin.
- **Calls made in this part where the decisions are silent**, each pinned by a test:
  - an unmeasured place takes own's median run, and when own has none either, the median of every measured run;
    a place with measured runs and no measured start starts at 0; with no run measured anywhere the run goes to
    own, or, when own is dropped, to the first place left;
  - no place left always writes the `wait` on `test_place`; it is polled only while a place with capacity is busy
    with live runs, since a place that is off, lacks the OS, has no capacity, or refused or vanished earlier in the
    same run never clears, and otherwise it ends at once and the run is refused;
  - `own` is per machine (a store merged from another machine adds nothing to this machine's own load or
    measurements), while machines and GitHub are shared by every repo;
  - the live count is read, not reserved: two runs deciding at the same moment may both take a place's last free
    slot, because a lock would need the server or the shared file ADR 0001 rejected.
