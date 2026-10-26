# Workflow

## States

- **A parent** (a ticket with open sub-tickets) carries a category and no state; its sub-tickets carry the states.
- **A parked ticket** (ORCH waits on a run it started instead of sitting on it) has its `ready-for-agent` removed and no state until the run's result puts `ready-for-agent` back.

## Labels

Each ticket carries one label of the set.

### The parked label

`parked` means: waiting on a named event; not worked until it comes. A parked ticket keeps its state label.
