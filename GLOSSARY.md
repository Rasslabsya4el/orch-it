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
