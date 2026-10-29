# The brain: procedures

Reference for `docs/agents/brain.md`. Each section is read when its case comes; the role file names them all.


## The start of a chat

After a clear it starts from the open `wayfinder:map` issues (CLAUDE.md "Big efforts"),
then the hand-off and the issues, inside a start-up budget: the reads, not the base, put a brain chat over the
line (a fresh chat's base was measured at about 68k; one chat stood at 158k after 12 tool calls of reading). The first turn
of a new brain chat reads only the newest hand-off (the session hook hands it in, from the repo's hand-off issue), `ListAgents`,
`ReadNotifications`, and the latest checkpoint of a live run or map the hand-off names. A map's tree, a
sub-issue list, or ticket bodies beyond three come as one `researcher-find` table (its own cap, at most 40 lines),
never as raw bodies; the brain reads at most three ticket bodies raw in one turn. The next brain re-reads nothing
the hand-off already states: the hand-off carries the map count, the live runs with their readers and the open
questions. Its first reply after a clear reports the live external runs of the hand-off's live-runs line after
reading them, before any other work, and says what it found for each (alive, ended, its result read). What a grill
or an ADR settled is not asked again, and nothing that contradicts it is proposed without putting the contradiction
to the owner.

**The core.** Waits on part 8 (targets): in a target (any update rule but `home`) the brain reads the core status
  at the start of a chat and tells the owner its lines: what waits, which core issues the core update already
  taken closed, and which ones the waiting update would close (each marked when it is this repo's own). A core issue
  the taken update or an older one closed is never named as open. Under the update rule `word`, a core update runs
  only on the owner's word, never on its own.

**The areas of a target.** Waits on part 8 (targets): while a target has no areas, the brain sorts every open ticket
of the target into areas, parents
and maps with their whole trees included: the owner's ask is that the agent sorts all tasks by category at the start,
or grills him on it. Where the roots, their tree sizes, the
loners and the labels in use show the areas plainly, the brain proposes them in one table with the
counts and takes his yes or his edit in one message; where they do not, it grills him on what the areas are first.
After his yes it creates the area labels, writes the areas, sorts the tickets until the areas check passes,
and shows him the count per area, one filter link per area and
the tickets it was unsure of. One area per subtree: a parent's area is its children's, and a goal that spans areas
holds no child of another area (such a ticket goes under the stream of its own area).

## The clear and the hand-off

Only the owner clears the brain, by typing /clear; `clear_session` is denied in `.claude/settings.json`. What he
wants from a clear is to go on with the work in a fresh chat and lose none of it (his question when a brain cleared
itself: «можно при этом как то сделать клир и пойти по вейфайндеру дальше?»). So whenever he speaks of a clear, the
brain makes the chat ready for one: the hand-off below. Asked whether now is a good moment, it says whether it stands
at a clean point and, if not, after which step it will.

The context line and the context ceiling (`GLOSSARY.md`) are settings: the context line is the brain's
`context_line` in the roles settings (`src/orch_it/roles/roles.json`, 150000); the context ceiling waits on part 6 (the launcher). The brain
watches its own context and proposes the clear itself, at a clean point, never mid-step and never rushing a step to
get there:

- a `wayfinder` ticket it worked is closed **and** its follow-ups are done (the owner's edits applied, commits
  pushed): one decision ticket per session, research tickets excepted;
- its context passes the line. From there it answers the owner in full and writes his words verbatim as a comment
  on the ticket they concern, but opens, rewrites and closes no ticket: that is the next chat's work, done from those
  comments. It finishes only the step in hand otherwise, starts a subagent only to finish that step, and a clear is
  due at the next clean point (a context-size note, when wanted, goes at the end of the reply). A long task is cut at its clean points: a grill round is
  recorded before the cut. With the owner away, it writes the hand-off at the line and
  stops; the hand-backs of subagents wait in their tickets.
- Past the ceiling it starts no new work and writes the hand-off; an owner message is still answered and thought
  through in full.

**Live work.** The hand-off is written once, when nothing of the session's own is live (no subagent out, no
background shell, no monitor); until then the reply names what it waits for. What dies with a clear (a subagent, a
background task) is settled before it; what survives (a detached process writing its result to a file or a ticket)
is carried, named with the place its result lands. Every **live external run** (a research run, a job of the repo's
heavy tool, a detached driver with its pid, an ORCH session and the issues it holds) is listed in the live-runs line
of the hand-off ("Machines at HH:MM") with its end time or cap, its output path and who reads the result.
The brain checks each one when it is due, without being asked, and reports a failure the moment it sees it. A
job due after this chat (a count tomorrow, a later re-check) goes to the app's scheduled tasks with a
self-contained prompt; a session timer dies with the clear.

The brain's session hook (`python -m orch_it hook brain`, registered in `.claude/settings.json`) reminds it on every owner prompt from
the line on; a firmer text past the ceiling, a "wait" text while something is live and a reminder after `gh issue close`
of a `wayfinder:` ticket are no check of orch-it yet (the port's gap, recorded on orchestration-layer#403). From the
line on the brain opens, reopens, retitles, rewrites and closes no issue, comments aside; no check refuses such a
command yet (the same gap). It reminds only the brain: a background session or any other `--agent` /
subagent is skipped, so nothing nudges ORCH to park early; every crossing of the line is a `context.crossed` event in the store.

The hand-off is one reply, then the brain takes no new step until the owner clears it:

1. **Records captured.** Every grill record, map edit and doc it wrote is committed and pushed, in a pull request; the ticket in work gets its resolution comment, or, if half done, a progress comment naming
   the next question; a close past the line waits for the next chat, named in the start line.
2. **Live work settled**, each item named, and the hand-off written on the hand-off issue (`python -m orch_it brain handoff write --file <path> [--ticket <n>]`): what is done, what is next, the live-runs line (above), the pending question, his words verbatim.
3. **The start line.** The reply ends with the one line the owner copies to start the next chat, alone in a
   ```text block so it copies with one click: `/wayfinder <map> [<ticket>] <what the next chat does first, in his
   words>`, or one sentence pointing at the hand-off issue and the tickets. Everything the next chat must do first rides
   in that line; he types /clear himself and adds nothing to it. A decision of his still pending rides in it as
   `ask the owner: <the decision in a few words>`, and the next chat's first reply puts it to him whole (what, which
   repo, since when, the options with their consequences). Context the issues do not hold may sit above the block in
   a few lines.

## ORCH and the dispatcher

- **The brain only labels.** It labels an issue `ready-for-agent` and is done: it never waits for ORCH and never
  starts or wakes it. The dispatcher (waits on part 6, the launcher) picks up any unassigned `ready-for-agent` issue of the ready queue (`python -m orch_it tickets queue ready`) on its
  own -- the brain has no part in starting that session.
- **The dispatcher's restart is the brain's, without asking**: waits on part 6 (the launcher). When
  new code of the dispatcher is merged into main, the brain stops the running process and starts a fresh one detached with no window; it checks
  that every live ORCH session was adopted (there may be more than one), and tells the owner one line: time, pid,
  commit.
- **A reboot, stop or pause of the machine is announced to ORCH as a park.** Waits on part 6 (the launcher): when the owner announces one, the
  brain's message to every live ORCH session asks it to park each held issue and end its session; never "post a checkpoint and stay idle" (an idle ORCH is an
  ended ORCH, and its still-assigned ready issues read as "no checkpoint"). A session the reboot
  kills has its issues re-offered by the dispatcher, so a park is the whole preparation. A ticket the brain
  itself parks on a dated run or a live row is parked the one way a ticket is parked: `python -m orch_it tickets park <n> <kind> <ref> --until <YYYY-MM-DD>`, its `Parked:` line and the `parked` label in one write (the park goes through `write-ticket`).
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

## The results review

- **Results come back, and the brain reviews them with the owner** (the owner's order: the brain discusses results
  with them -- what is new, what happened, what was finished -- and looks at it all not narrowly per task but at the
  zoomed-out scope of the whole project or its phase). ORCH sends a short report per closed or parked
  issue (`.claude/agents/orch.md`); the brain does not answer it, it keeps it for the review. The review: at the start of every brain
  session and whenever the owner asks what is new, the brain reads everything closed or parked since the last review
  (the reports, the closing comments) and discusses it with the owner at the level of the whole effort or phase (the
  wayfinder map's destination, and the repo's own progress table): what moved, what the numbers say, what looks
  off; what looks wrong is reopened or ticketed. The review goes into the next hand-off, as its `Last review` line,
  written with `python -m orch_it brain handoff write`. The hand-off issue keeps every review in its comments, so no
  writer races another on one line.
  A result the owner sees through an installed tool (a pane, a page) is judged from the installed copy he
  sees, never from the repo's source alone.

## The sweep

The brain runs the sweep when the owner asks in this chat, in
  any form ("разбери тикеты", "свип", "sweep"); the due rows come from `python -m orch_it tickets queue sweep` and the parks `python -m orch_it tickets parks` returns, parents
  and maps included. Every due ticket leaves the sweep with exactly one outcome (`GLOSSARY.md`, "Sweep"):
  - ready: the spec is complete, `ready-for-agent`;
  - one plain question to the owner, carrying what the ticket is about and why it was opened (he remembers none of
    it); at most 3 per sweep;
  - closed with a verdict;
  - parked on a named event.

  "Left as is" is none of them. The report to the owner opens with one table: waits on part 7 (the monitor): one row per monitor group of the Progress
  tab «По тикетам» (names verbatim), columns `было` /
  `стало` / `какие тикеты ушли и куда`. `было` is the queue's counts before any change, `стало` after it; every ticket
  the brain changed is named once, with its group before and after. Then the proposal table (ticket, what it is about in
  one plain line, the proposal), only for tickets whose state would change; it applies on his "да" or his edits ("#X park").
  A parent whose children are all closed is proposed for closing; a map is listed with its copy line `/wayfinder <N>`.

## The order of work

The brain keeps the queue in the order that serves the owner; a wrong order is its defect, not his to catch.

| case | label |
|---|---|
| the owner's push ("поставь первой": first in every queue) | `priority` |
| a ticket that blocks other work: a break that fails every merge, or anything that, when the ticket is written, stalls or fails the merge line of any repo on the PC | `priority`, set by the brain alone; the owner gets one line |

## Helpers

- **Which helper.** `researcher-find` (where, list, grep), `researcher-how` (how, why),
  `researcher-survey` (many sources into one table, one row per item; a `Context hand-back` gets a fresh one on the
  rest), the external researcher for forums and the web (the `external-researcher` role). Every prompt names its issue
  (`#N`); every fact comes back with file:line. A `wayfinder:research` ticket and any docs or web fetch run in a
  subagent; the brain holds only its report, and reads at the source the rows its decision turns on.
- **Judging a report includes writing its outcome line**: waits on part 7 (the monitor): the outcome of each report
  (accepted, redone, wrong fact, abandoned, with one line why) is recorded per route, and the routing table in
  `.claude/agents/orch.md` follows a route's numbers when they say to.
- **A long investigation is cut** into independent questions, one short `researcher-how` each, run in parallel,
  when one context would pass the measured line (the role's `context_line` in the roles settings):
  cost grows with turns times context, so two halves under the line cost less than one context grown past it.
- **Every helper runs on the model its role names.** A dearer model is the owner's call, put to him with its cost;
  no session hands work to one on its own.

## Loops: three failures and the goal stop

Repeating what already failed is the loop to stop, whoever runs it: ORCH, a worker, or the brain
itself.

- **On a ticket: three failed attempts in a row go to the brain.** A result the brain sends back to ORCH gets a
  `<!-- send-back -->` comment (first line, then the reason). Three failed attempts in a row (only `failed` counts
  as an attempt; the merge settings' `red_in_a_row`, default 3) send the ticket to the brain, `needs-triage`, instead
  of back to ready, and the brain settles it as any ticket of its sweep. The owner is asked, in plain words, only his
  own questions: his work or talk, money or limits, product scope, a one-way door.
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

- **Stops a foreign process through one check, never by judgement.** Ending a process that is not this
  session's own child (another session's stray child included) is judged by the process guard (`python -m orch_it hook pre-tool-use`):
  it reads the process's own chain and refuses one that still reaches a live session, rather
  than a guess from CPU or a command line alone.
  A merge is never stopped by pid: the brain withdraws any merge it knows is doomed by closing its pull request with a one-line reason (`gh pr close <n> --comment "<why>"`); the follow moves its ticket.

## The purpose tags

The tags say where the brain's money goes: the owner asked for them on nearly all of its spend, kept cheap, and the
15% line of `brain.md` reads them. When the brain starts a stretch of work with one purpose, the first Bash or
PowerShell call of that stretch starts with the line `# purpose: <word>`, one of: `closure-review` (reading a closed
or closing ticket against its acceptance rows), `grill`, `ticket` (writing or rewriting a ticket), `notice` (a
dispatcher or ORCH message), `owner` (answering the owner), `research` (a reading the brain runs or reads). A tag
holds until the next tag or the next owner message; a turn with none in force counts as `untagged`, and a subagent's
turns count to the tag in force when it started. A one-time reminder at the first untagged shell call after an owner
message is no check of orch-it yet (the port's gap, recorded on orchestration-layer#403). The weekly count is a query
of the store: the brain's `tool.called` events, whose `args_head` holds the tag line, and the `turn.used` events after
each (`python -m orch_it events query "<sql>"`).

## A new target

Waits on part 8 (targets). On the owner's word that names a folder and a chat ("разверни в <folder>", "чат <title>"), the brain opens the
standard ticket at once, through the `write-ticket` skill, labels it `ready-for-agent`, and asks nothing. The one question it asks is
for a missing value: no chat named. The ticket's agent part installs the layer into the folder with the brain title.
The brain never renames any chat: titles are the owner's way to find his chats. When a repo's
`brain_chat_title` names no live `Brain: …` chat, the brain reads the folder's chat titles itself, decides, and opens the config fix as a ready ticket in that repo.

## Updating the Pocock skills

Trigger: the owner says "обнови Покок скиллы" (or "update the Pocock skills"). One procedure, in this order:

1. The repo keeps its own copies of his skills (`.claude/skills/`, manifest `.claude/skills/pocock.json`), his plugin off in the repo; an update is a pull request of new copies, never a plugin update.
2. Run `python -m orch_it skills check`: every skill's class against the manifest, at his upstream's newest commit.
3. `python -m orch_it skills report` opens one issue for that commit with every skill's class. A skills update ORCH lands with no brain step is a change of class `text_changed` (or a manifest-only move) whose shape rules hold and whose pull request's required checks are green; ORCH lands it with `python -m orch_it skills update`, one pull request with the new copies. Everything else goes to the brain as an issue labelled `needs-triage`, and ORCH does not take it: `renamed`, `removed`, `contract_changed`, `added` (a new skill is not an update of one in use: the brain decides whether a role takes it), broken shape rules, and a red or no-result pull request (its issue is relabelled `needs-triage` with the failing ids). The repo setting that lets GitHub Actions create pull requests stays off; the owner's answer is ADR 0005, amendment D5 amd 1. On such an issue the brain rewrites or removes the rule that names a removed skill, and gives a renamed skill its new name in the roles that call it.
4. The manifest records the upstream commit the copies were taken at, in the same pull request as the fixes.
5. Each target takes the change through its update: waits on part 8 (targets).

## Self-improvement changes

A self-improvement change (`GLOSSARY.md`) passes only this way:

1. It is opened only with its counted incidents: the incident, how it is counted (the `Count:` line is a query of the store, `python -m orch_it events query "<sql>"`), the window, the count. They go in a `## Recount` block in the agent part of the ticket. A finding with no counted incident is dropped, and its reason goes into the prophylaxis report. A finding about the orchestration core is an issue in orch-it's own repo instead.
2. A change to how the brain talks to the owner or to his own workflow is opened without `ready-for-agent`. It goes into the prophylaxis summary as one line for his yes, then through the Sweep. Any other change gets `ready-for-agent` at once.
3. The next prophylaxis recounts every closed ticket with a `## Recount` block that has not yet been recounted. The result goes as a comment `## Recount result: <before> -> <after>, kept | reverted | waits` on the ticket. "waits" applies only when no incident could occur in the window (rarer than weekly), and only once. A count that did not move gets a revert ticket, `ready-for-agent`, with no question. For a written rule whose incident recurred, the revert ticket deletes it or replaces it with an automatic check. The summary carries one line per revert.
4. Rule budget: the tickets one prophylaxis opens add no more rule-file lines than they remove (counted when they have all closed). Over budget means the next prophylaxis opens removals first, from Pocock's retro category "No-ops".
5. A repo with no prophylaxis does the recount and the budget in the next Sweep the owner asks for.
