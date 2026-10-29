# The guards judge the object, own a process by its tree, and fail open with an event

Status: amended by D4 amd 1, D4 amd 2 (Rasslabsya4el/orchestration-layer#413) and D4 amd 3 (orchestration-layer#453); the decision text below is kept as it was.

The three kept guards — worktree delete, foreign process stop, private data in a public repo —
decide on the object a command would act on (the real path on disk with links followed and the
file system's own case rule, the real process tree, the real visibility of the remote), not on a
list of command words; a process is own only when its session id is in its environment, its
parent chain (each parent older than its child, so a reused pid never counts) reaches the
session's agent, or the store recorded this session starting it; and a guard that crashes lets
the command through and writes `error.raised` with `failed_open: true` rather than blocking it
(Rasslabsya4el/orchestration-layer#395, decisions 1, 3, 7). We judge the object because the old
layer's guards judged text and 32 breaks came from it (a verb list misses `unlink` and
`find -delete`, a folder-name pattern misses a worktree elsewhere, a text kill rule cannot tell
own from foreign). We own a process by the tree because own-versus-foreign is a fact of the
process tree, not of the command text, and the environment id is what survives Git Bash's dead
parents and a detached child. We fail open because a crashed guard that blocks every command
stops all work in every repo — the "breaks at any sneeze" pain of the map — and the event makes
the crash visible and countable.

## Considered Options

- **Judging the command's words (a verb and path denylist), as the old `permission_guard` did**:
  a word list is a second thing to keep complete, and every gap is an irreversible delete or a
  foreign kill that slipped through; the trigger set stays only to say "this command can delete
  / stop / push", and the verdict is read from the object.
- **Owning a process by its parent chain alone**: Git Bash records a dead parent for every
  child, so a chain can prove nothing; and a reused pid would make a foreign process look own
  without the start-time check.
- **Failing closed (a crashed guard blocks the command)**: safer against a single leak, but one
  bug then halts every session in every repo with no way through; the event plus failing open
  keeps work moving and surfaces the crash.

## Consequences

- **A refusal is final.** A guard deny ends the command; ORCH re-plans or asks the brain
  (the pipeline model's `unit.refused`). A false fire is a finding to fix in this part, never a
  bypass, and is marked through `guards reverse` so false fires are counted from the store.
- **Every triggered command writes `guard.fired`** (deny or pass) through part 1's writer; a
  command no trigger matches writes nothing. The kind, verdict and location are written, never
  the private value.
- **psutil is the one reading of the process tree**, orch-it's first runtime dependency, so the
  ownership fact reads the same on Windows, Linux and macOS.

## D4 amd 1: the repo a guard's events are written under

Amendment (Rasslabsya4el/orchestration-layer#413): a guard's events are written under the repo it runs in, the GitHub
origin, else the setting `repo`. With neither, the verdict still stands and stderr names the event not written; no repo
is guessed, because a default repo is an account value this repo refuses.

## D4 amd 2: a named bad setting is refused, not failed open

Amendment (Rasslabsya4el/orchestration-layer#413, brain decision on item 3): a named bad setting is refused, not failed
open. A `human_language` with no table or no language code makes the tickets hook refuse the `gh issue create` or body
edit it judges, with a reason naming the setting and the missing table, because there is a way through (set the
language or add the table). Any other unexpected error still fails open.

## D4 amd 3: where human_language lives

Amendment (orchestration-layer#453): the setting `human_language` lives only in the `settings.json` at the root of the git work tree, and is `en` when that file or key is missing or there is no git tree. A command reads the git top level of its working directory, then that tree's file. The installation's settings file no longer holds the setting: a `human_language` key there is refused with `HumanLanguageError`, which names the tree file to move it to. A port or a skills copy never copies the tree file, so another repo gets `en` until it commits its own. This amendment replaces nothing in amd 2: a language with no table is still refused as amd 2 says.
