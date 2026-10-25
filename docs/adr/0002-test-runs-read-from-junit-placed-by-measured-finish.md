# A test run is read only from its JUnit report, placed by measured finish with load counted from the store, and the full suite stays the required gate

A test run's outcome comes from the JUnit XML report the repo's test command writes, never from its exit code or its text output; the place rule sends each run where it is expected to finish first, counting load as the live runs in the store; and the required checks `linux` and `windows` run the full suite on every pull request, with no selection and no split (Rasslabsya4el/orchestration-layer#394, decisions 1, 5 and 6). We chose JUnit XML because pytest, jest-junit, go-junit-report, gotestsum and Maven Surefire all write it, so a stranger's repo in any language is read the same way; load from the store because it reads the same on Windows, Linux and macOS and a decision replays from the store alone; the full suite because orch-it's suite runs in minutes and GitHub caps concurrent jobs per account, so a split adds queue rather than removing it.

## Considered Options

- **Reading pytest's text output or exit codes**: binds the layer to one tool, and reads a stranger's JavaScript or Go repo as `summary_missing` or as green.
- **The OS's CPU load as the load measure**: differs per OS, changes between the decision and the run, and cannot be replayed afterwards.
- **Test selection or a split into parts for the required checks**: a selection map is a second source of truth to keep correct, and a selection would let a required check pass on tests it never ran.

## Consequences

- **A run says red only from a complete report holding a red case.** A cut run, a missing or half-written report, 0 collected, or a requested id not collected is `no_result`, never `failed`; a red case wins over a missing id.
- **Expected finish = median measured start + median of the last 10 passed or failed runs of the repo and selection.** A place with no measured run takes the own place's median, or, when own has none either, the median of every measured run; a place with runs but no measured start starts at 0; with nothing measured the run goes to own, or, when own is dropped, to the first place left. Ties go own, then machines in settings order, then GitHub.
- **Only a busy place is waited for.** With no place left, the run waits on `test_place` only while a place with capacity is busy with live runs; a place that is off, lacks the OS, has no capacity, or refused or vanished earlier in the same run never clears, so the run is refused at once.
- **`own` is per machine; machines and GitHub are shared.** A store merged from another machine adds nothing to this machine's own load or measurements.
- **The live count is read, not reserved.** Two runs deciding at the same moment may both take a place's last free slot and run there together, one over its capacity; a lock would need a server or a shared file the store was built to avoid (ADR 0001).
- **Slowness is answered by measurement.** `test-places` shows the median run and GitHub queue minutes per place and OS; when either nears the bound, a ticket is opened from those numbers.
