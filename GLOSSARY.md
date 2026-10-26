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

**Clean environment**:
What a test run's process sees of its environment: the OS's base variables and the repo's own test settings, nothing of whoever started it.
_Avoid_: sandbox, isolated environment

**Bound**:
The longest a test run or a research run may take; a run that reaches it is cut, which is never a failed run. A
research run also has a stall bound: the longest its session tree may go without a new step.
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
The short name a brief asks for (`luna`, `terra`), which the settings map to the provider's model id.
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
writes; its rules that matter are held by checks, not by its sentences.
_Avoid_: prompt, agent doc, instructions

**Roles settings**:
Each role's model, effort, fallback model, inputs and report rules, in one place every repo shares.
_Avoid_: role config, model table

**Fallback model**:
The model a role's session takes when its own model's safeguard pauses it.
_Avoid_: backup model, second model

**Session**:
A role that runs as its own conversation, started by the owner or the launcher: the brain and ORCH.
_Avoid_: main agent, chat

**Subagent**:
A role started inside a session for one piece of work, with its inputs filled at its spawn.
_Avoid_: helper, child agent, sub-session

**Spawn**:
The one way a subagent starts: its inputs filled from one place and its prompt built from them, or a refusal that
names the input it lacks.
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
The check at a subagent's stop that sends its report back once when a report line has no call behind it, an owed
line is missing, or the report breaks its cap or its context line.
_Avoid_: stop hook, gate, validator
