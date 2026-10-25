# Events are appended to one file per writer and read through an index rebuilt from the files

Every event of every role, machine and OS is appended as one JSON line to the writing process's own file,
`<store>/<YYYY-MM-DD>/<writer id>.jsonl`, and read through a SQLite index that is only a cache of those files
(Rasslabsya4el/orchestration-layer#393, decisions 1-6 and 9). We chose it because it needs no lock and no server on
any OS, a crashed writer loses at most its last line, and two machines' stores merge by copying files. The line format
is what is hard to reverse: every later writer writes it and every installed store already holds it.

## Considered Options

- **One shared SQLite file as the write target**: a lock across processes, fragile Windows file locking, and two
  machines' databases do not merge.
- **A hosted database**: an account and a secret for every stranger who installs the layer, against "works anywhere".

## Consequences

- **Ids carry the merge.** Nothing keys an event by file or line: a live event's id is time-ordered random, an
  imported one's a hash of its source record, so re-importing writes nothing and copied files unite by id.
- **The index is disposable**, so it may change shape at will: deleting it and rebuilding gives the same rows.
- **One file defines the closed sets** (kinds, step ids, outcomes, reason codes) and every event is checked against
  it before it is written; `detail` stays open, so a new field needs no store change, while a new code does.
- **The store is per machine and never in a git work tree**, the default the per-user data folder, so events never
  reach a commit.
- **Money is read from transcripts**, which exist for every launch form, and priced from a dated price file; a model
  without a price gets no number, never a guess.
- **Cost is also a share of the weekly subscription limit**, through a setting with no built-in number (limits
  differ per plan and change), re-derived from the store between two readings of the usage card.
