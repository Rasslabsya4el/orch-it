# orch-it

The orchestration layer: Claude sessions, scripts and GitHub working tickets in a repo. Its work is read and fixed
from its events, never from a new hand measurement.

## Events

**Event**:
One fact of the layer's work at one moment, written once and never changed.
_Avoid_: log line, record, entry

**Kind**:
The name of what an event is, one of a closed list.
_Avoid_: type, event type, category

**Envelope**:
The fields every event has whatever its kind.
_Avoid_: header, metadata, common fields

**Detail**:
The fields of an event beyond its envelope, particular to its kind; their set stays open.
_Avoid_: payload, data, extra

**Step pair**:
The two events of a step that takes time, its start and its end, under one step id.
_Avoid_: span, interval, duration event

**Outcome**:
How a step ended, one of a closed set.
_Avoid_: status, result, verdict

**Reason code**:
A word from the closed list beside a field, saying why.
_Avoid_: reason text, cause, message

## Storage

**Store**:
All the events of one machine, for every repo on it.
_Avoid_: database, event log

**Writer**:
One process that adds events to the store.
_Avoid_: producer, emitter, logger

**Index**:
The queryable copy of a store, derived from it alone.
_Avoid_: cache table, read model

## Test runs

**Test run**:
One call of a repo's test command at one test place on one commit, judged from the report it writes.
_Avoid_: test job, suite run, CI run, build

**Test place**:
Where a test run goes: this machine, another machine named in the settings, or GitHub.
_Avoid_: runner, host, target, executor

**Place rule**:
The one rule that picks a test run's place from the runs live and the times measured in the store.
_Avoid_: scheduler, routing, placement policy

**Priority**:
The per-repo whole number `github_priority` ranking the repos of one account for the account's GitHub machines: while the
account's live jobs are at its concurrency, a repo's line starts no run while a repo of the same account with a higher
one has a ready member; equal priorities hold nothing.
_Avoid_: weight, rank, precedence

**Availability**:
The readiness verdict of the `github` test place: whether it may be offered (Actions on, its request budget with room,
GitHub readable, for a private repo the account's included minutes not spent, no job of the account waiting in the queue past
its bound), its machines busy of their limit, whether a run there costs money (a private repo's does, so the place rule
ranks it after the places that are free), and the note the decision carries (its busy-of-limit count, or why it is off).
A place off because its runs would not start also carries the cause: Actions off, minutes spent, or queue wait past the bound.
_Avoid_: status, health, state

**Queue wait**:
The time a GitHub job waits from its queued to its started, the wait for a machine; read from the job's own times, never
from the run's status.
_Avoid_: queue time, pending time, waiting time

**Poll**:
The layer's repeated read of GitHub or a machine until a result appears. The polls that go through the request budget (the
merge line's waits for a head's required checks, for a turn behind a higher-priority repo and for a member's run, and a
test run's read of its jobs on GitHub) wait longer while the budget is under its threshold; the line-lock wait waits the
setting `poll_seconds` and the check that a landing is marked merged waits a fixed interval, and neither slows with the budget.
_Avoid_: watch, wait loop, refresh

**Request budget**:
The account's GitHub API calls per hour, counted by the layer in the store (one `github.called` event a call); GitHub's
own limit is read from `rate_limit`, and a poll waits longer while the remaining falls under the threshold, the setting
`request_budget_threshold`, whose default is in the defaults file beside the budget code. The Actions token keeps a budget of its own per repo.
_Avoid_: rate limit, quota, API budget

**Placeholder file**:
A stub a test place puts where a file of private data should be, because that place does not have the real one; a test that read one has not been run on the real file until it reruns at a place that has it.
_Avoid_: mock file, fixture, dummy data

**Clean environment**:
What a test run's process sees of its environment: the OS's base variables and the repo's own test settings, nothing of whoever started it.
_Avoid_: sandbox, isolated environment

**Foreign run**:
A run of orch-it's suite and its setup commands in a copy that is not the owner's setup: an invented account and repo, a
folder with another name, no git identity, no GitHub variable, a fresh home. The foreign job of each OS (see
`.github/workflows/tests.yml`; the script is `.github/scripts/foreign_run.py`) runs it: the Linux and Windows jobs per change
and on the nightly, the macOS one on the nightly only. Per change the Linux job runs the not-slow, not-live tests and the
Windows job only those of them marked as touching the OS; on the nightly every job runs everything but the live tests.
_Avoid_: sandbox run, clean run, outside run

**Bound**:
The longest a test run or a research run may take; a run that reaches it is cut, which is never a failed run. A
research run also has a stall bound: the longest its session tree may go without a new step. The merge line's run, its
wait for its head's checks, its wait for the required checks on a batch's sha and the wait for its line lock each have a
bound of their own. The bound check's bound is
a median instead: the median run time of the newest `LAST_RUNS` ended test runs at a test place and OS (passed or failed
ones), or a GitHub place's median queue wait over the same window; a median past its bound opens one `needs-triage`
ticket, and a later crossing comments on it.
_Avoid_: timeout, limit, deadline

## Research

**Research bridge**:
The one way the layer sends a brief to an outside model and gets its research report back.
_Avoid_: gpt-research, agentops research, GPT tool

**Front**:
One way into the research bridge: the command line or the MCP server; both run the same research run.
_Avoid_: entry point, wrapper, interface

**Research run**:
One brief sent through the research bridge, from its start to its end, on a server of its own.
_Avoid_: research job, GPT run, query

**Brief**:
The text a research run sends: its questions, the sources it wants, and where its research report goes.
_Avoid_: prompt, query, task

**Research report**:
The text a research run keeps as its answer: the longest text the model wrote, alone or joined across a compaction
that cut it, that passes the report check.
_Avoid_: answer, output, last message, result

**Report check**:
The tests a text passes to be a research report, in order: length, then links, then a Reddit link unless the brief
asks for primary sources.
_Avoid_: validation, acceptance, quality gate

**Partial report**:
What the model wrote in a research run that ended with no research report, kept beside where the report would go.
_Avoid_: draft, partial result

**Compaction**:
The outside tool replacing a long session's history with a summary in the middle of a research run and telling the
model to continue.
_Avoid_: summarization, context reset

**Helper session**:
A session the model starts under its own during a research run, or under another helper session, at any depth.
_Avoid_: subagent, sub-session, child task

**Session tree**:
A research run's own session and every helper session under it; the run's cost is the whole tree's.
_Avoid_: session family, call tree

**Model alias**:
The short name a brief asks for (`small`, `big`), which the settings map to the provider's model id.
_Avoid_: model name, model id

**Job**:
A research run started by the MCP front and read back later by its id.
_Avoid_: task, ticket, request

## Skills

**Skill copy**:
One of Pocock's skills held in the repo, byte for byte as his upstream had it at the upstream pin.
_Avoid_: vendored skill, fork, plugin skill

**Skill of ours**:
A skill the layer itself wrote, held in the repo beside the skill copies.
_Avoid_: custom skill, own skill

**Upstream pin**:
The one commit of Pocock's upstream that every skill copy of a repo comes from.
_Avoid_: version, release, tag

**Skill contract**:
What a skill takes and returns: how it is named, described and invoked, the skills it calls, and the parts of it that
say what it reads, writes or produces.
_Avoid_: interface, API, signature

**Update class**:
What a move of the upstream pin did to one skill, one of a closed set: `same`, `added`, `renamed`, `removed`,
`text_changed`, `contract_changed`.
_Avoid_: diff type, change kind

**Shadowing**:
A skill in a user's own skills folder taking the place of a repo skill of the same name, in every session of that user
in that repo.
_Avoid_: override, conflict, clash

## Guards

**Guard**:
A check that reads what a command would act on — the real path, the real process tree, the real visibility of a remote — and refuses the real damage, deciding on the object, not on the words of the command.
_Avoid_: permission, filter, policy, rule

**Protected root**:
A folder a guard will not let a command delete or move or reach through a link: a worktree of the repo, the main checkout among them, or the repo's common `.git`.
_Avoid_: safe path, protected directory, allowlist

**Own process**:
A process this session started, proven by its session id in its environment, its parent chain to the session's agent, or the store's record of it; any other is foreign and a session may not stop it.
_Avoid_: child, owned pid, our process

**Private data**:
What must not reach a public repo, measured on the machine: the home folder in every spelling, the host name, a secret by its published shape, a non-public email, or a word of the per-machine private list.
_Avoid_: secret, PII, sensitive data

**False fire**:
A guard refusal a human or the brain marks as wrong; counted from the store through `guard.reversed`, never from memory.
_Avoid_: false positive, misfire, bad block

**Finding**:
One place (file, line, kind) where the scan meets a value of a kind the coding standard refuses, outside an allowed place; a push or a ticket with a finding is refused.
_Avoid_: hit, match, leak

**Exceptions list**:
`guards-exceptions.toml` at the repo root; each entry names a file, a line pattern, the kinds of finding it excuses and its reason; an entry with no kinds or no reason, or a malformed file, refuses the push.
_Avoid_: exemption, ignore file, waiver

**Allowed place**:
Where a value of a kind the coding standard refuses may stand: the settings file, the records (`docs/research/` and `docs/adr/`), an entry of the exceptions list. A path inside the repo is not a value.
_Avoid_: safe place, exempt path, excluded folder

## Tickets

**Ticket**:
One issue of a repo written in the layer's form: a human block, then an agent block.
_Avoid_: task, card, story

**Human block**:
The five lines a person decides from, in the owner's language: what changes, the goal, the plan, the result, the undo.
_Avoid_: summary, TL;DR, abstract

**Agent block**:
The spec an agent works from: facts with their sources, the rule, the acceptance rows and the line fields.
_Avoid_: description, body, spec section

**Acceptance row**:
One check of a ticket with what passing it means.
_Avoid_: criterion, test case, requirement

**After merge**:
The mark of an acceptance row that can only be checked once the change is on the main line.
_Avoid_: post-merge check, deferred row

**Outside case**:
A member of the ticket's class of cases that none of its named cases covers, checked by one acceptance row.
_Avoid_: edge case, extra case

**Same object**:
The writer's account of every other part that acts on what the change touches, and what happens when both run.
_Avoid_: dependencies, impact, side effects

**Weight**:
How much work a ticket is, on a scale from 1 to 10.
_Avoid_: size, estimate, points

**Shape rule**:
One rule a ticket's form must keep before an agent may take it.
_Avoid_: lint, validation, template check

**Violation**:
One shape rule a ticket breaks.
_Avoid_: error, problem, finding

**Ready**:
A ticket offered to agents; making a ticket ready is the moment its shape is judged again.
_Avoid_: approved, open for work

**Label set**:
The labels every repo of the layer carries, each with one meaning.
_Avoid_: label config, taxonomy

**Park**:
A ticket's wait on one named event until a last day, after which it goes back to its queue or to triage.
_Avoid_: block, hold, snooze

**Park kind**:
Which event a park waits on: a pull request ending, a ticket closing, a day, a run ending, the owner's answer, a layer version.
_Avoid_: park type, trigger

**Park line**:
The one line at the top of a parked ticket naming its park, written and removed only together with the parked label.
_Avoid_: parked note, wait marker

**Park reader**:
The one reader that looks at every park and returns, sends to triage or leaves waiting each parked ticket.
_Avoid_: unparker, dispatcher, poller

## Roles

**Role**:
One kind of agent of the layer with one job: the brain, ORCH, a worker, a reviewer of one axis, a researcher.
_Avoid_: agent type, persona, bot

**Role file**:
The text a role works from: its job, its inputs, its steps with the skill each calls, its report and the events it
writes.
_Avoid_: prompt, agent doc, instructions

**Roles settings**:
Each role's model, effort, fallback model, inputs and report rules.
_Avoid_: role config, model table

**Fallback model**:
The model a role works on when its own model's safety check pauses its work.
_Avoid_: backup model, second model

**Session**:
A role that runs as its own conversation, started by the owner or the launcher: the brain and ORCH.
_Avoid_: main agent, chat

**Brain title**:
The exact title of the owner's session that is the brain in a repo; the layer knows the brain by it.
_Avoid_: brain name, chat name

**Hand-off**:
What a brain session leaves for the next one: what is done, what is next, the live runs, the pending question, the owner's words.
_Avoid_: state entry, handover, summary

**Hand-off issue**:
The one pinned issue of a repo that holds its hand-offs, one per comment; never a ticket.
_Avoid_: state file, hand-off ticket

**Subagent**:
A role started inside a session for one piece of work, with its inputs filled at its spawn.
_Avoid_: helper, child agent, sub-session

**Spawn**:
The one way a subagent starts, with each of its inputs in hand before its first step.
_Avoid_: delegation, dispatch, handoff

**Input**:
One thing a role needs before its first step: the ticket, the unit, the diff, the worktree or the scratchpad.
_Avoid_: argument, parameter, context

**Plan**:
ORCH's split of a ticket into units, one row each with the test that shows it done.
_Avoid_: task list, breakdown

**Unit**:
One row of a plan, done by one subagent and judged by its test.
_Avoid_: task, subtask, chunk

**Report line**:
A line of a subagent's final report that states a skill it called at its step, or that it skipped it and why.
_Avoid_: status line, skill line, footer

**Report cap**:
The most lines a role's final report may have.
_Avoid_: length limit, max lines

**Context line**:
The context size past which a role hands its work back as it stands instead of going on.
_Avoid_: context limit, token budget

**Stop hold**:
The check at a subagent's stop that sends its report back once when the report breaks its role's report rules.
_Avoid_: stop hook, gate, validator

## Merge

**Merge line**:
The order in which a repo's green pull requests land on its main line, one at a time, the oldest ready first.
_Avoid_: merge queue, merge train, merge window

**Batch**:
The members the merge line combines on one branch from `main` for one full pass: a batch lands, all of it, when the pass
is green and every name of the required checks is green on its sha; a red test or a red check name splits it in halves, unless every red id or name is also red on main's
head when the batch was made: then the batch holds, its members wait for main's next sha, and none is sent back.
_Avoid_: group, bundle, train, queue

**Required checks**:
The check names a repo's merge is held to, the setting `required_checks`; the merge line lands a batch only when each is
green on its exact sha, whichever place posted it, and a base's branch protection accepts or refuses the update of `main`.
_Avoid_: required jobs, check list, gate

**OS-free check**:
A required check name that no job of the workflow carries on an OS, such as a scan of the pull request or the commit e-mail
check: the suite's place covers it wherever the suite runs. A name the workflow cannot place on an OS at all is neither
(`classify_checks`, `testruns/github_place.py`).
_Avoid_: platform-free check, OS-less job

**OS-bound check**:
A required check name that is a job of the workflow running on one OS; only a place of that OS covers it, and GitHub
posts it when no place of the settings runs that OS (`classify_checks`, `testruns/github_place.py`).
_Avoid_: per-OS check, OS job

**Line lock**:
The one hold on a repo's merge line: while one line runs, a second waits for it and names who holds it.
_Avoid_: merge turn, mutex, merge lock

**Stale lock**:
A line lock held longer than any line runs; it is reported and never taken, and only a person removes it.
_Avoid_: dead lock, expired lock

**Measured rows**:
What ORCH found for each acceptance row on its branch before the pull request: pass, fail, not applicable with its
reason, or after merge.
_Avoid_: test results, acceptance results

**Acceptance comment**:
The layer's comment on a ticket holding its measured rows and what they were measured on; the ticket is closed from it.
_Avoid_: closing comment, summary comment

**Ready mark**:
The layer's mark on a pull request that it entered the merge line, and when; not the ready of a ticket.
_Avoid_: queued label, ready label

**Left mark**:
The layer's mark on a pull request that it left the merge line, and why; the next ready mark puts it back.
_Avoid_: dequeued, removed from queue

**Follow mark**:
The layer's mark on a ticket saying what its follow did with it; red returns in a row are counted from them.
_Avoid_: status comment, bot note

**Follow**:
What the layer does with a ticket when its pull request ends, is stopped by a person or goes red: closes it, returns
it, parks it or sends it to the brain.
_Avoid_: sync, webhook handler, hand-back

**Red-on-main ticket**:
The ticket of tests that fail on the main line, opened by the layer; a pull request red only on them waits for it. Not Red on main, which names a required check that is red on main's head.
_Avoid_: red main list, skip list, known failures

**Red on main**:
A required check name that failed on the head main had when a batch was made (a name main has no result for, or that is
green or pending there, is not red on main). When every red name of a batch is red on main, the batch holds and none of
its members is sent back (`name_red_on_main`, `merge/line.py`). Not the Red-on-main ticket, which lists test ids, not check names.
_Avoid_: known red, skipped name, main-red check
