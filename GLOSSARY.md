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
