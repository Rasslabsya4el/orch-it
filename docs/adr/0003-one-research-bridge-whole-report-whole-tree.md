# One research bridge: a server per run, the report chosen from every message, the cost of the whole tree

A research run goes through one module, `orch_it.research`, whatever front starts it (the command line or the MCP
server `orch-it-research`), and each run starts an opencode server of its own on a free port, with its own password and
folder under orch-it's data folder, and kills that server's whole process tree when it ends
(Rasslabsya4el/orchestration-layer#396, decisions 1-6). The research report is chosen from every assistant message of
the run, not the last one, and the run's cost is summed over its whole session tree at any depth. Two bridges had grown
apart (a shared server with a status file and a meter, and a private server per job with neither); one lost a finished
38,973-character report to a compaction that came after it (#380), and both metered the top session only, missing
907,170 tokens of helper sessions in one run (#380 F3).

## Considered Options

- **One shared server per machine** (the old CLI bridge): saves a start of a few seconds per run, but needs a state file
  every run trusts, a restart rule when settings change, and lets two runs of different callers share a process; a
  server per run is gone with its run, so a bound or a crash leaves nothing behind and two bridges never meet.
- **The last assistant message as the report** (both old bridges): right until opencode compacts after the report
  and the model answers the injected "Continue..." turn in one line.
- **Joining every text across a compaction**: glues that one-line reply onto the finished report; texts are joined only
  across a compaction that cut a message (one that did not finish), and a summary of the compaction is never a
  candidate.
- **Metering the top session, or its direct children**: misses a helper of a helper; the walk follows every child at
  any depth, from opencode's list and from each `task` call's own record.

## Consequences

- **No model id, key or machine path in the code.** The aliases, the provider table, the key's variable, the bounds and
  the state folder are settings; the key is read from the environment at run time (the MCP server gets it through
  `.mcp.json`'s `${VAR}` expansion), never from a file or the registry.
- **A refusal costs nothing.** An alias not in the settings, an unset key, no opencode or a report path outside the work
  tree is refused before any event or server.
- **Progress is in the store**, as `step.progressed` events of the run, not in a status file; a bounded run (time or
  stall) ends `no_result` with its partial report beside the report path, and a report path that cannot be written
  keeps the report in the run's folder.
- **The stall is judged on the monotonic clock** from the last growth of the tree's step count, so a wall clock jumping
  forward never stalls a working run; the time bound reads both clocks, as test runs do, so a machine that sleeps
  through it still cuts the run.
