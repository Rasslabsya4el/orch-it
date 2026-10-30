# The merge line holds auto-merge off on its waiting members; tickets follow from GitHub alone

Status: superseded by ADR 0009 (Rasslabsya4el/orchestration-layer#414); the decision text below is kept as it was.

Green pull requests land on a repo's main line through the merge line, one run of `python -m orch_it merge line` at a
time per repo under a line lock, on the machine and under the owner's `gh`, so its updates and merges start the
required checks (Rasslabsya4el/orchestration-layer#400, decisions 2 and 3). The line serves its oldest ready member:
brings it up to date, waits for its checks of its head sha, merges it at that sha. Every other member that is up to
date with the base has its auto-merge turned off while it waits and gets it back once it is behind, is the head, or
the run ends, after a follow of it. Branch protection with strict checks lets GitHub's auto-merge merge any green
up-to-date pull request at once, so with four sessions writing one repo whichever finished first merged, every other
one fell behind before its checks ended, and a green pull request waited for hours (#355, 2026-10-26).
GitHub's merge queue would order them, but GitHub offers it to repos owned by organizations only: orch-it, a
personal repo, has none, and a personal private repo gets no branch protection at all on the owner's plan.

What a pull request's ticket does next is read from GitHub alone: `Refs #N`, the ticket's park, and the layer's marks
in its comments (the acceptance comment, the ready mark, the left mark, the follow mark), written by the owner's login
or a workflow's bot and never taken from anyone else's comment. The follow runs wherever it is called (a workflow,
the line, the sweep, the park reader) and writes the ticket first, through #398's write that refuses a ticket changed
since it was read, so two follows of one moment move the ticket once.

## Considered Options

- **Auto-merge alone, the line only bringing heads up to date**: the head loses its run each time a fresh up-to-date
  pull request finishes first; four pull requests made ready at once merge in the order their checks end.
- **The line as the only merger, auto-merge never on**: nothing merges while no line runs (the launcher down, the
  machine asleep), and `pr open`'s "auto-merge on" (decision 1) would mean nothing.
- **The line's order and holds kept in the store**: the store is one machine's; a follow in a workflow or on another
  machine could not tell a held member from one a person stopped.
- **GitHub's merge queue**: not offered to a personal repo; ordering, batching and re-running are its own then, and
  the line would only have to read it.

## Consequences

- **A held member waits with auto-merge off.** A line killed mid-run leaves its held members without auto-merge; their
  ready marks keep them in the line, and the next run gives it back. A pull request made ready by hand (auto-merge on,
  no ready mark) that a killed line held drops out of the line until someone turns auto-merge on again.
- **Out of order only before the line runs.** Pull requests that turn green and up to date before any run of the line
  sees them are merged by GitHub as they come; the line orders what it sees.
- **One machine's lock.** The line lock is a folder in the per-user data folder: two machines running the line on one
  repo would not see each other's lock.
- **The workflows' events land in the store later.** A follow, a main check or a sweep in a workflow writes to the
  run's own store, uploaded as an artifact; the sweep on the machine brings those files into the machine's store.
