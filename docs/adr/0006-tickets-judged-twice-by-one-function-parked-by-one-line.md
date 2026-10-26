# A ticket is judged by one shape function twice, and a park is one line with one reader

A ticket's form is judged by one function, `orch_it.tickets.violations`, at two places: a PreToolUse hook refuses a
`gh issue create` or `gh issue edit` with any violation (and a create in a session that never called the
`write-ticket` skill), and a workflow on `issues` events takes `ready-for-agent` off a ticket that breaks a rule and
comments every violation, never editing the body. A park is one line, `Parked: <kind> <ref> until <YYYY-MM-DD>`, of
one of six event kinds, written only by `python -m orch_it tickets park` together with the `parked` label, and
returned only by one reader, `python -m orch_it tickets parks` (Rasslabsya4el/orchestration-layer#398, decisions 3,
4, 5). One function in both places, because the old layer's guard judged only what passed through a session and a
label set on github.com passed nothing; GitHub as the second place, because it sees every write whatever made it.
One line and one reader, because the old free `Parked: <event>` text was read by nothing that could tell when its
event came, and a ticket parked on a merged pull request never returned (#384 PARK-1..12).

## Considered Options

- **The shape checked only in the session**: a ready label set on github.com, by `gh api` or by another client is
  never judged.
- **The shape checked only on GitHub**: a bad ticket is found after it is written, and a session learns of it from a
  comment instead of a refusal at the moment of writing.
- **A park as a label alone, or as a field of a project board**: neither names the one event and its date in a form
  every machine reads from the ticket itself, and a board is one more thing to keep in step.

## Consequences

- **A park is a `wait` step of the store.** Its run id is the ticket and the moment its label went on, read from
  GitHub, so the reader on any machine (or a scheduled GitHub job, part 6) ends the step the command started.
- **The reader never overwrites a person.** It reads the ticket again just before each write and leaves it, for the
  next run, when it changed; a person's comment on a parked ticket returns it to the brain.
- **Known gaps.** The hook watches `gh issue create` and `gh issue edit`; a body written by `gh api` or another
  client is judged on GitHub only when the ticket is made ready. A `Parked:` line written on github.com is found by
  the reader (GitHub's search may lag it by a minute) and sent to the brain.
