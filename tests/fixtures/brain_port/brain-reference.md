# The brain: procedures

Reference for `docs/agents/brain.md`. Each section is read when its case comes; the role file names them all.


## The start of a chat

After a clear it starts from the open `wayfinder:map` issues (CLAUDE.md "Big efforts"),
then `docs/STATE.md` and the issues, inside a start-up budget: the reads, not the base, put a brain chat over the
line (a fresh chat's base was measured at about 68k; one chat stood at 158k after 12 tool calls of reading). The first turn
of a new brain chat reads only the hand-off entry of `docs/STATE.md` (after `git fetch origin`), `ListAgents`,
`ReadNotifications`, and the latest checkpoint of a live autopilot or map the entry names. A map's tree, a
sub-issue list, or ticket bodies beyond three come as one `researcher-find` table (its own cap, at most 40 lines),
never as raw bodies; the brain reads at most three ticket bodies raw in one turn. The next brain re-reads nothing
the hand-off entry already states: the entry carries the map count, the live runs with their readers and the open
questions. Its first reply after a clear reports the live external runs of the hand-off's machines line after
reading them, before any other work, and says what it found for each (alive, ended, its result read). What a grill
or an ADR settled is not asked again, and nothing that contradicts it is proposed without putting the contradiction
to the owner.

**The core.** In a target (any `update_rule` but `home`) the brain runs `python -m tools.agentops core
  status` at the start of a chat and tells the owner its lines: what waits, which core issues the core update already
  taken closed, and which ones the waiting update would close (each marked when it is this repo's own). A core issue
  the taken update or an older one closed is never named as open. Under `word`, `core update` runs only on the owner's
  word, never on its own.

**The areas of a target.** While `config/orchestration.json`'s `areas` is empty (`core status` says so; a port writes
it empty and `core update` never touches it), the brain sorts every open ticket of the target into areas, parents
and maps with their whole trees included: the owner's ask is that the agent sorts all tasks by category at the start,
or grills him on it. `python -m tools.agentops areas propose` lists the roots with the size of their trees, the
loners and the labels in use. Where these show the areas plainly, the brain proposes them in one table with the
counts and takes his yes or his edit in one message; where they do not, it grills him on what the areas are first.
After his yes it creates the labels (`gh label create area:<name>`), writes the `areas` setting, sorts the tickets
with `areas apply <plan>` until `areas check` exits 0, and shows him the count per area, one filter link per area and
the tickets it was unsure of. One area per subtree: a parent's area is its children's, and a goal that spans areas
holds no child of another area (such a ticket goes under the stream of its own area).

## The clear and the hand-off

Only the owner clears the brain, by typing /clear; `clear_session` is denied in `.claude/settings.json`. What he
wants from a clear is to go on with the work in a fresh chat and lose none of it (his question when a brain cleared
itself: «можно при этом как то сделать клир и пойти по вейфайндеру дальше?»). So whenever he speaks of a clear, the
brain makes the chat ready for one: the hand-off below. Asked whether now is a good moment, it says whether it stands
at a clean point and, if not, after which step it will.

The context line and the context ceiling (`GLOSSARY.md`) are settings in `config/orchestration.json`:
`context_line` (default 150000) and `context_ceiling` (default 200000; a target without it reads 200000). The brain
watches its own context and proposes the clear itself, at a clean point, never mid-step and never rushing a step to
get there:

- a `wayfinder` ticket it worked is closed **and** its follow-ups are done (the owner's edits applied, commits
  pushed): one decision ticket per session, research tickets excepted;
- its context passes the line. From there it answers the owner in full and writes his words verbatim as a comment
  on the ticket they concern, but opens, rewrites and closes no ticket: that is the next chat's work, done from those
  comments. It finishes only the step in hand otherwise, starts a subagent only to finish that step, and a clear is
  due at the next clean point (a context-size note, when wanted, goes at the end of the reply). A long task is cut at its clean points: a grill round is
  recorded before the cut. With the owner away and no autopilot on the goal, it writes the hand-off at the line and
  stops; the hand-backs of subagents wait in their tickets.
- Past the ceiling it starts no new work and writes the hand-off; an owner message is still answered and thought
  through in full.

**Live work.** The hand-off is written once, when nothing of the session's own is live (no subagent out, no
background shell, no monitor); until then the reply names what it waits for. What dies with a clear (a subagent, a
background task) is settled before it; what survives (a detached process writing its result to a file or a ticket)
is carried, named with the place its result lands. Every **live external run** (a research run, a job of the repo's
heavy tool, a detached driver with its pid, an ORCH session and the issues it holds) is listed in the machines line
of the brain's State entry ("Machines at HH:MM") with its end time or cap, its output path and who reads the result.
The brain checks each one when it is due, without being asked, and reports a failure the moment it sees it. A
job due after this chat (a count tomorrow, a later re-check) goes to the app's scheduled tasks with a
self-contained prompt; a session timer dies with the clear.

`tools/hooks/brain_reset_nudge.py` (registered in `.claude/settings.json`) reminds it on every owner prompt from
the line on, with a firmer text past the ceiling and a "wait" text while something is live; it also reminds after
`gh issue close` of a `wayfinder:` ticket. From the line on it refuses the brain's commands that open, reopen,
retitle, rewrite or close an issue; comments pass. It reminds and refuses only the brain: a background session or any other `--agent` /
subagent is skipped, so nothing nudges ORCH to park early; every nudge and skip is a line in
`var/agentops/brain_nudge.log`.

The hand-off is one reply, then the brain takes no new step until the owner clears it:

1. **Records captured.** Every grill record, map edit and doc it wrote is committed and pushed; the ticket in work gets its resolution comment, or, if half done, a progress comment naming
   the next question; a close past the line waits for the next chat, named in the start line.
2. **Live work settled**, each item named, and the machines line written (above).
3. **The start line.** The reply ends with the one line the owner copies to start the next chat, alone in a
   ```text block so it copies with one click: `/wayfinder <map> [<ticket>] <what the next chat does first, in his
   words>`, or one sentence pointing at `docs/STATE.md` and the tickets. Everything the next chat must do first rides
   in that line; he types /clear himself and adds nothing to it. A decision of his still pending rides in it as
   `ask the owner: <the decision in a few words>`, and the next chat's first reply puts it to him whole (what, which
   repo, since when, the options with their consequences). Context the issues do not hold may sit above the block in
   a few lines.

## ORCH and the dispatcher

- **The brain only labels.** It labels an issue `ready-for-agent` and is done: it never waits for ORCH and never
  starts or wakes it. `tools/agentops/dispatch.py` picks up any unassigned `ready-for-agent` issue on its
  own -- the brain has no part in starting that session.
- **The dispatcher's restart is the brain's, without asking**: when
  new code of the dispatcher is merged into main, the brain stops the running process (`agentops stop` on either of its two, the `cmd` wrapper or its python, ends both) and runs
  `dispatch --ensure` (a live one prints `live pid <pid> commit <sha8>`; an old commit means stop it), which starts a fresh one detached with no window; it checks in `artifacts/runs/dispatch.out`
  that every live ORCH session was adopted (there may be more than one), and tells the owner one line: time, pid,
  commit.
- **A reboot, stop or pause of the machine is announced to ORCH as a park.** When the owner announces one, the
  brain's message to every live ORCH session asks it to park each held issue (the parking checkpoint,
  `ready-for-agent` off, unassigned) and run `finishing`; never "post a checkpoint and stay idle" (an idle ORCH is an
  ended ORCH, and its still-assigned ready issues read as "no checkpoint"). A session the reboot
  kills has its issues re-offered after `dispatch --ensure`, so a park is the whole preparation. A ticket the brain
  itself parks on a dated run or a live row writes the same first line: `<!-- parked-on-run: <what it waits for> -->`. On a
  machine-checkable event it also writes the `Parked:` line at the top of the body (`workflow.md`, "The sixth label"; the body rewrite goes through `write-ticket`).
- `needs-info`: ORCH labels the issue, writes the question in a checkpoint comment and messages the brain. The brain
  settles it with the owner, writes the answer into the issue, and relabels it `ready-for-agent`; the dispatcher
  picks it up from there.
- **What executes goes to ORCH.** Work that does rather than decides (code, a run, a data move, a `wayfinder:task`
  the agent can do alone) is ORCH's, as a ready issue, never done in the brain chat or a side chat the brain starts:
  that is what the orchestration is for. For a map task the brain opens one execution issue (`write-ticket`, label
  `ready-for-agent`, the map ticket linked in its body) carrying the map ticket's Done-when rows and any limit on where
  the work and its data may go; the map ticket keeps only its `wayfinder:` label, stays open and assigned to the
  brain. When ORCH closes the issue, the brain posts the resolution on the map ticket from the issue's closing numbers
  (the comment names the issue), closes it and adds the map's Decisions line. A task where the owner must act or
  answer stays with the brain.

- **In machine mode `play` the brain stays answerable and starts nothing.** The mode is one file per user (the
  machine doc); the brain's chat is an interactive session, so it is never frozen and keeps talking to the owner. It
  starts no subagent, no ORCH, no worker, no run and no wake while the mode is `play`, and it never sets `normal`
  itself: only the owner's word or his button does (`python -m tools.agentops machine normal`). What it would
  start waits for `normal`; `ready-for-agent` labels may still be set, the dispatcher starts nothing until then.

## The results review

- **Results come back, and the brain reviews them with the owner** (the owner's order: the brain discusses results
  with them -- what is new, what happened, what was finished -- and looks at it all not narrowly per task but at the
  zoomed-out scope of the whole project or its phase). ORCH sends a short report per closed or parked
  issue (`orch.md`); the brain does not answer it, it keeps it for the review. The review: at the start of every brain
  session and whenever the owner asks what is new, the brain reads everything closed or parked since the last review
  (the reports, the closing comments) and discusses it with the owner at the level of the whole effort or phase (the
  wayfinder map's destination, and the repo's own progress table): what moved, what the numbers say, what looks
  off; what looks wrong is reopened or ticketed. The review goes into `docs/STATE.md`'s `Last review` line,
  written under the merge lock through `python -m tools.agentops commit -m <message> --review-entry "<text>"
  docs/STATE.md` (with `--state-entry "<text>" --state-as <writer>` in the same call when the session's own
  `Updated` line changes too). Never a plain `git commit`/`git push` on `main` in the main checkout, and never a
  hand edit of the `Updated` or `Last review` line: the command brings the checkout up to `origin/main` itself and
  writes under the lock, so two writers never race the same line. The `Last review` line keeps the newest review
  and at most two `Before it:` entries; the command drops older ones (git holds them).
  A result the owner sees through an installed tool (a pane, a mod, a page) is judged from the installed copy he
  sees, never from the repo's source alone (for a mod: `mods status <source>` -> 0 differ first).

## Handing a goal to the autopilot

When the owner leaves with a goal (one task, a set of issues or a map), the brain hands it to the autopilot
(`docs/agents/autopilot.md`) in this order:

1. **Leaving grill.** A couple of rounds of the questions likely to come up while he is away, only of his kind
   (what he wants, his taste, what he accepts; `brain.md`, "The owner"): conditional ones on research still open,
   the likely ones only, no fanaticism. The owner ends it when he likes; a question left
   unasked is decided as any question born during the stretch (`docs/agents/autopilot.md`, "The chat brain during a stretch").
2. **Terms.** The owner's autopilot terms (scope, deadline, usage percentage, any hard stop he lifts by name) are
   echoed back in one line and agreed before he goes.
3. **Label.** Only then the `autopilot` label goes on the goal's issue.

One goal has one worker at a time, the autopilot or the brain, never both. The take-back: when the owner starts
working the goal in this chat, the brain sends the autopilot "стоп", waits for its checkpoint (bounded: at most
10 min; past that, the brain tells the owner it has not stopped and works nothing on the goal), and only then works
with him. A message about the autopilot ("как там", "что сделал") is answered from its latest checkpoint; a message on
another topic stops nothing.

## The sweep

The brain runs the sweep when the owner asks in this chat, in
  any form ("разбери тикеты", "свип", "sweep"); the due rows come from `python -m tools.agentops sweep list`, parents
  and maps included. Every due ticket leaves the sweep with exactly one outcome (`GLOSSARY.md`, "Sweep"):
  - ready: the spec is complete, `ready-for-agent`;
  - one plain question to the owner, carrying what the ticket is about and why it was opened (he remembers none of
    it); at most 3 per sweep;
  - closed with a verdict;
  - parked on a named event.

  "Left as is" is none of them. The report to the owner opens with one table: one row per monitor group of the Progress
  tab «По тикетам» (names verbatim, as `sweep list` prints them in its group-count lines at its end), columns `было` /
  `стало` / `какие тикеты ушли и куда`. `было` is `sweep list`'s counts before any change, `стало` after it; every ticket
  the brain changed is named once, with its group before and after. Then the proposal table (ticket, what it is about in
  one plain line, the proposal), only for tickets whose state would change; it applies on his "да" or his edits ("#X park").
  A parent whose children are all closed is proposed for closing; a map is listed with its copy line `/wayfinder <N>`.

## The order of work

The brain keeps the queue in the order that serves the owner; a wrong order is its defect, not his to catch.

| case | label |
|---|---|
| the owner's push ("поставь первой": first in every queue) | `priority` |
| a ticket that blocks other work: a break that fails every merge, or anything that, when the ticket is written, stalls or fails the merge line of any repo on the PC | `priority`, set by the brain alone; with `hard-push` too when the running batch is lost anyway (main red, or the batch fails on that break); the owner gets one line |
| the owner's word to cut the running batch | `hard-push`, with `priority` |
| a Flow fix the brain writes | `flow-fix`, checked against the row above before its labels are set; the owner gets one line |

## Helpers

- **Which helper.** `researcher-find` (Haiku: where, list, grep), `researcher-how` (Sonnet 5.5: how, why),
  `researcher-survey` (many sources into one table, one row per item; a `Context hand-back` gets a fresh one on the
  rest), the external researcher for forums and the web (`docs/agents/research.md`). Every prompt names its issue
  (`#N`); every fact comes back with file:line. A `wayfinder:research` ticket and any docs or web fetch run in a
  subagent; the brain holds only its report, and reads at the source the rows its decision turns on.
- **Judging a report includes writing its outcome line**: `python -m tools.agentops outcome --issue N --route
  <researcher type or opencode model> --class <accepted|redone|wrong-fact|abandoned> --why "one line" [--session
  <id>]`. The routing table in `docs/agents/orch.md` is `python -m tools.agentops routes` over this log; the brain
  reruns it weekly (alongside `token_meter.py --orch-threshold`) and updates that table when a route's numbers say to.
- **A long investigation is cut** into independent questions, one short `researcher-how` each, run in parallel,
  when one context would pass the measured line (`python scripts/token_meter.py`, the numbers in the local file):
  cost grows with turns times context, so two halves under the line cost less than one context grown past it.
- **Every helper runs on the model its role names.** A dearer model is the owner's call, put to him with its cost;
  no session hands work to one on its own.

## Loops: the unblocker and the goal stop

Repeating what already failed is the loop to stop, whoever runs it: ORCH, the autopilot, a worker, or the brain
itself.

- **On a ticket: three failed attempts in a row go to an unblocker.** A result the brain sends back to ORCH gets a
  `<!-- send-back -->` comment (first line, then the reason). Three failed attempts in a row since the newest answer on
  the ticket (`<!-- owner-answer -->`, the owner's words verbatim, or `<!-- brain-answer -->`; send-backs and merge
  results that did not land) start an unblocker on Fable 5.1 high (`docs/agents/orch.md`; the owner's choice, the model
  best at pushing a task through), which writes the `Loop stop` and the way for ORCH to carry out and decides nothing
  of the owner's; the brain writes neither. The owner is asked, in plain words, only his own questions: his work or
  talk, money or limits, product scope, a one-way door, or a second stop on the ticket naming the same reason as the
  last `Loop stop`.
- **In the brain's own work and the agents it watches**: the third try of a thing that failed twice the same way
  (a command, a read, a reply, a worker's fix) is not made; the brain says what failed and re-thinks, or says it
  cannot.
- **On a goal: three fix cycles stop the fixes for a retro.** A fix cycle is a ticket opened to fix what the
  previous ticket on the same goal of the owner left, while the goal's number is still not reached. After the third
  cycle in a row the brain opens no further fix for that goal: it reads everything since the goal started (tickets,
  merge results, idle stretches), writes a short retro as a comment starting `<!-- goal-stop -->` on the goal's
  ticket or map (what was tried, what each cycle found, where the time went), and re-plans the approach as one ticket
  before any further fix.

## Stopping a foreign process

- **Stops a foreign process through one command, never by judgement.** Ending a process that is not this
  session's own child (another session's stray child included) goes only through `python -m tools.agentops
  stop <pid> ...`: it prints the process's own chain and refuses one that still reaches a live session, rather
  than a guess from CPU or a command line alone.
  A merge is never stopped by pid: the brain withdraws any merge it knows is doomed, with `python -m tools.agentops merge withdraw <issue|branch> --why "<one line>"`.

## The purpose tags

The tags say where the brain's money goes: the owner asked for them on nearly all of its spend, kept cheap, and the
15% line of `brain.md` reads them. When the brain starts a stretch of work with one purpose, the first Bash or
PowerShell call of that stretch starts with the line `# purpose: <word>`, one of: `closure-review` (reading a closed
or closing ticket against its acceptance rows), `grill`, `ticket` (writing or rewriting a ticket), `notice` (a
dispatcher or ORCH message), `owner` (answering the owner), `research` (a reading the brain runs or reads). A tag
holds until the next tag or the next owner message; a turn with none in force counts as `untagged`, and a subagent's
turns count to the tag in force when it started. The first untagged shell call after an owner message gets a
one-time reminder (`tools/hooks/brain_read_nudge.py`). The weekly count: `python -m tools.agentops purposes --week`.

## A new target

On the owner's word that names a folder and a chat ("разверни в <folder>", "чат <title>"), the brain opens the
standard ticket at once, through the `write-ticket` skill, from `config/core_templates/new_target_ticket.md` with
`{folder}` and `{title}` filled in, labels it `ready-for-agent`, and asks nothing. The one question it asks is
for a missing value: no chat named. The ticket's agent part is `python -m tools.agentops target add --folder <path>
--brain-title "<title>"`, which ORCH runs at the home (`docs/agents/PORTING.md`, "A new target in one command").
The brain never renames any chat: titles are the owner's way to find his chats. When a repo's
`brain_chat_title` / `brain_chats` names no live `Brain: …` chat, the brain reads the folder's chat titles and the
dispatch log itself, decides, and opens the config fix as a ready ticket in that repo.

## Updating the Pocock skills

Trigger: the owner says "обнови Покок скиллы" (or "update the Pocock skills"). One procedure, in this order:

1. Update the plugin with the plugin manager's own update. Never copy a skill into the repo (Pocock as shipped).
2. Run `python -m tools.agentops core pocock-check`: one finding per line, exit 1 on any finding.
3. Each finding becomes a core issue at the home (`python -m tools.agentops core issue`) and is fixed there: a removed skill means its rule is rewritten or removed; a renamed one takes the new name.
4. Write the plugin version last verified into `verified_plugin_version` of `tools/agentops/pocock_contract.json`, in the same commit as the fixes.
5. Each target takes the change through its update rule: `python -m tools.agentops core status` names what each is behind on, `core update` rolls it out.

## Self-improvement changes

A self-improvement change (`GLOSSARY.md`) passes only this way:

1. It is opened only with its counted incidents: the incident, how it is counted (the `Count:` line is `python -I scripts/counts/<file> [args]`, a script kept in `scripts/counts/`, one file per counted incident, named `<issue>_<what>.py`; while it is not on main, the ticket's agent block names where it is (a pushed branch and path, or a path ORCH can read) and the ticket's first unit copies it into `scripts/counts/`), the window, the count. They go in a `## Recount` block in the agent part of the ticket. A finding with no counted incident is dropped, and its reason goes into the prophylaxis report. A finding about the orchestration core is a core issue at the home instead, see `docs/agents/retro.md`.
2. A change to how the brain talks to the owner or to his own workflow is opened without `ready-for-agent`. It goes into the prophylaxis summary as one line for his yes, then through the Sweep. Any other change gets `ready-for-agent` at once.
3. The next prophylaxis recounts every closed ticket with a `## Recount` block (from the `recount` command) that has not yet been recounted. The result goes as a comment `## Recount result: <before> -> <after>, kept | reverted | waits` on the ticket. "waits" applies only when no incident could occur in the window (rarer than weekly), and only once. A count that did not move gets a revert ticket, `ready-for-agent`, with no question. For a written rule whose incident recurred, the revert ticket deletes it or replaces it with an automatic check. The summary carries one line per revert.
4. Rule budget: the tickets one prophylaxis opens add no more rule-file lines than they remove (the `rules --issues` command, counted when they have all closed). Over budget means the next prophylaxis opens removals first, from Pocock's retro category "No-ops".
5. A repo with no prophylaxis does the recount and the budget in the next Sweep the owner asks for.
