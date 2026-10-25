# orch-it

The orchestration layer: Claude sessions, scripts and GitHub working tickets in a repo. Everything the layer does is
recorded as events, so its work is read and fixed from the record, never from a new hand measurement.

## Events

**Event**:
One fact of the layer's work at one moment, written once and never changed.
_Avoid_: log line, record, entry

**Kind**:
The name of what an event is, `<noun>.<verb_past>` in lower case, from a closed list.
_Avoid_: type, event type, category

**Envelope**:
The fields every event carries whatever its kind: who, where, when, about which repo and ticket.
_Avoid_: header, metadata, common fields

**Detail**:
The fields of an event beyond its envelope, particular to its kind; a writer may add one without changing the store.
_Avoid_: payload, data, extra

**Step pair**:
A step that takes time, written as two events, its start and its end, sharing one step id.
_Avoid_: span, interval, duration event

**Outcome**:
How a step pair ended, one of a closed set of six: passed, failed, no_result, refused, lost, stopped.
_Avoid_: status, result, verdict

**Reason code**:
A word from a closed list written next to its field, saying why; free text never stands in for one.
_Avoid_: reason text, cause, message

## Storage

**Store**:
All the events of one machine, for every repo on it, kept as files only ever appended to.
_Avoid_: database, log, event log

**Writer**:
One process appending events to its own file of the store.
_Avoid_: producer, emitter, logger

**Index**:
A queryable copy of the store, rebuilt from the store's files whenever it is lost.
_Avoid_: database, cache table, read model
