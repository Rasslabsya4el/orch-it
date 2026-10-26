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
The longest a test run may take, set per repo; a run that reaches it is cut, which is never a failed run.
_Avoid_: timeout, limit, deadline

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
