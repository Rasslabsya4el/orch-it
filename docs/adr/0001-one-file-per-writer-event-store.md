# Events are appended to one file per writer and read through a SQLite index rebuilt from the files

The layer records every event of every role, machine and OS (Rasslabsya4el/orchestration-layer#393). Each writing
process appends JSON Lines to its own file, `<store>/<YYYY-MM-DD>/<writer id>.jsonl`, one validated event per line;
everything is read through a SQLite index that is only a cache of those files. The line format is the decision that
is hard to reverse: every writer of every later part writes it, and every store already on a machine holds it.

## Considered Options

- **One shared SQLite file as the write target.** Rejected: many processes writing one file need a lock across
  processes, Windows file locking makes that fragile, and two machines' databases do not merge.
- **A hosted database.** Rejected: every stranger who installs the layer would need an account and a secret, against
  "works anywhere".
- **Files per writer, an index built from them** (chosen): no lock and no server on any OS; a crashed writer loses at
  most its last line; two machines' stores merge by copying files, because the index unites events by `id`.

## Consequences

- **Ids carry the merge.** A live event's id is a UUID v7 (time-ordered random); an imported event's id is a hash of
  (source, record id, kind), so importing the same record again writes nothing. Of two lines with one id, the one at
  the lowest (file, offset) is the row, so a rebuilt index equals the incremental one. Nothing keys an event by file
  or line number.
- **The index is disposable.** Deleting it and rebuilding gives the same rows. It reads new complete lines only,
  leaves a half-written last line for the next refresh, and rebuilds itself when a file shrank, changed its head or
  vanished, when the closed sets change, or when SQLite cannot read it. Table `events` has the envelope as columns
  (`at` in UTC at a fixed width, so text order is time order), `detail` as JSON, and every detail field the event
  list names as a generated column, so the list's queries run as written. User SQL runs read-only.
- **The closed sets live in one file.** `src/orch_it/events/closed_sets.json` is the only place a kind, step id,
  outcome or reason code is defined; every event is validated against it before it is written, and a breach raises
  with a code. `docs/events.md` is its human copy, kept in agreement by a test. `detail` stays open.
- **Where the store lives.** A setting, default the per-user data folder of the OS (`%LOCALAPPDATA%\orch-it\events`,
  `$XDG_DATA_HOME/orch-it/events` or `~/.local/share/orch-it/events`, `~/Library/Application Support/orch-it/events`),
  one store per machine for every repo, separated by the `repo` field. A folder inside a git work tree is refused
  before anything is written, so events never reach a commit.
- **Money is read from transcripts.** `turn.used` comes from the session's Claude Code transcript, which exists for
  every launch form, once per assistant message id; subagent transcripts are their own sessions with
  `parent_session` set. Prices come from `prices.json` with source and date per model, the 1-hour cache write and
  tiers included; a model without a price gets `usd` null with reason `price_missing`, never a guess.
- **Cost is also a share of the weekly limit.** Every cost printed for a Claude model shows `% week` beside the
  dollars: usd divided by the setting `weekly_limit_usd_per_pct` (dollars at API prices per 1 % of the weekly
  subscription limit, with the plan's name and the date it was measured). The setting has no built-in number: unset,
  the column says `not set`; a non-Claude model says `n/a`. Readings of the usage card are `value.measured` events
  (`weekly_limit_pct`), and `limit-rate` re-derives dollars per 1 % from the `turn.used` sums between two readings.
