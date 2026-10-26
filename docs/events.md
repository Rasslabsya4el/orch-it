# Events

The event store records every step, point event and measured value of the layer, one row per event, each with the same envelope.
`src/orch_it/events/closed_sets.json` is the only definition; this file is its human copy, kept in agreement with it by `tests/test_events_doc.py`.
Kinds, the envelope, outcomes and code lists are closed; `detail` is open, so a writer adds a detail field without a store change.

## Envelope

| field | type | null | codes |
|---|---|---|---|
| `id` | text | no |  |
| `kind` | kind | no |  |
| `at` | timestamp | no |  |
| `repo` | repo | no |  |
| `ticket` | int | yes |  |
| `role` | code | no | `role` |
| `session` | text | yes |  |
| `parent_session` | text | yes |  |
| `turn` | text | yes |  |
| `step` | step | yes |  |
| `run` | text | yes |  |
| `machine` | text | no |  |
| `os` | text | no |  |
| `layer_version` | text | no |  |
| `note` | text | yes |  |

## Code lists

| list | codes |
|---|---|
| `role` | `owner`, `brain`, `orch`, `worker`, `worker-strong`, `reviewer-standards`, `reviewer-spec`, `researcher-find`, `researcher-how`, `researcher-survey`, `external-researcher`, `launcher`, `github`, `hook`, `script` |
| `outcome` | `passed`, `failed`, `no_result`, `refused`, `lost`, `stopped` |
| `step_ended_any` | `suspended`, `lease_lost` |
| `launched_by` | `launcher`, `owner`, `session`, `boot` |
| `session_end_reason` | `turn_ended`, `idle`, `context_ceiling`, `killed`, `outage`, `stopped_by_owner`, `crashed` |
| `delivered` | `commit`, `comment`, `file`, `none` |
| `wait_reason` | `test_place`, `rate_limit`, `owner_answer`, `brain_answer`, `parked`, `blocker`, `review`, `github_queue`, `capacity` |
| `place` | `own`, `machine:<id>`, `github` |
| `test_os` | `linux`, `windows`, `macos` |
| `place_reason` | `earliest_finish`, `nothing_measured`, `forced` |
| `test_run_reason` | `red`, `cut`, `none_collected`, `ids_not_collected`, `summary_missing`, `job_missing`, `busy`, `os_missing`, `place_off`, `out_of_minutes`, `place_vanished`, `superseded` |
| `research_run_reason` | `too_short`, `few_links`, `no_reddit`, `model_error`, `no_reply`, `empty`, `time_bound`, `stalled`, `server_failed` |
| `report_from` | `last`, `earlier`, `joined` |
| `review_axis` | `standards`, `spec`, `edge` |
| `unit_reason` | `format`, `result`, `merge` |
| `pr_reason` | `merged`, `red`, `closed_unmerged`, `timeout`, `withdrawn` |
| `call_target` | `gh`, `git`, `github_api`, `bridge_http`, `claude_cli` |
| `check` | `pocock_contract`, `leak`, `guard_install`, `branch_protection`, `hooks_path`, `environment` |
| `claim_reason` | `held_by_other`, `not_ready` |
| `plan_reason` | `ticket_changed`, `not_doable` |
| `close_reason` | `acceptance_missing`, `row_failed`, `unanswered`, `already_closed` |
| `repair` | `lease_released`, `closed`, `unparked`, `rerun`, `red_ticket_opened` |
| `turn_reason` | `price_missing`, `usage_unreadable` |
| `load_verdict` | `code`, `load` |
| `hook_action` | `acted`, `skipped` |
| `run_check_result` | `alive`, `ended`, `failed` |
| `tool_outcome` | `passed`, `failed`, `refused` |
| `report_label` | `tdd`, `diagnosis`, `code-review`, `research`, `writing-for-agents`, `domain-modeling`, `codebase-design` |
| `context_threshold` | `context_line`, `ceiling` |
| `context_action` | `handed_off`, `new_work_taken`, `told` |
| `message_kind` | `notice`, `question`, `answer`, `handoff`, `report` |
| `ticket_field` | `title`, `body`, `labels`, `weight`, `priority`, `assignee`, `parent`, `blocked_by` |
| `release_reason` | `closed`, `parked`, `handed_off`, `session_ended` |
| `park_on` | `pr_ended`, `ticket_closed`, `date`, `run_ended`, `owner_answer`, `layer_version` |
| `unpark_by` | `event`, `hand` |
| `comment_kind` | `checkpoint`, `send_back`, `accepted`, `owner_answer`, `brain_answer`, `resolution`, `closing`, `machine` |
| `close_via` | `close_command`, `github_keyword`, `manual` |
| `state_reason` | `completed`, `not_planned` |
| `row_result` | `pass`, `fail`, `na` |
| `finding_fate` | `fixed`, `dismissed`, `open` |
| `launch_result` | `started`, `refused` |
| `guard` | `worktree_delete`, `foreign_process_stop`, `main_push_no_green`, `private_data_public` |
| `refused_by` | `worktree_delete`, `foreign_process_stop`, `main_push_no_green`, `private_data_public`, `permission` |
| `guard_verdict` | `deny`, `pass` |
| `doc_kind` | `glossary`, `adr`, `state`, `role_file`, `skill`, `core_block`, `research`, `memory`, `config` |
| `drift` | `behind`, `hook_extra`, `hook_missing`, `file_edited_in_target`, `leak` |
| `setting_source` | `repo`, `machine`, `default` |
| `resume_cause` | `suspend`, `hibernate` |

## Step ids

Each step id is written as `step.started` and `step.ended` with the envelope `step` set to the id.

| step id | started fields | ended fields | answers |
|---|---|---|---|
| `session` | `model`, `effort`, `agent_file`, `title`, `launched_by` (`launched_by`) | `reason` (`session_end_reason`), `held_tickets`, `checkpoint_written` | cost and time per session; why it ended; tickets left held by a dead session |
| `subagent` | `model`, `effort`, `agent_file`, `title`, `launched_by` (`launched_by`), `unit`, `worktree`, `prompt_has`, `prompt_chars` | `reason` (`session_end_reason`), `held_tickets`, `checkpoint_written`, `delivered` (`delivered`), `report_lines_owed`, `report_lines_present` | does a delegation give the subagent what it needs; subagents with no report |
| `wait` | `reason` (`wait_reason`), `on` | none | where the hours go between steps |
| `test_run` | `place` (`place`), `selection`, `commit`, `branch`, `pr`, `workers`, `settings_hash`, `part`, `run_os` (`test_os`), `places_offered`, `chosen` (`place`), `reason` (`place_reason`) | `reason` (`test_run_reason`), `collected`, `passed`, `failed`, `skipped`, `summary_seen`, `billed_minutes`, `start_minutes`, `run_minutes` | did the tests run where and on what; a cut run is no_result, never red |
| `review` | `axis` (`review_axis`), `unit`, `diff_ref` | `findings`, `fixed` | review cost and yield per axis |
| `unit` | `unit`, `assigned_role` (`role`), `model` | `commit`, `note_present`, `reason` (`unit_reason`) | a unit's life from assignment to hand-back or send-back |
| `pr` | `pr`, `branch`, `base`, `head` | `merge_sha`, `reason` (`pr_reason`) | time from PR to merge; PRs that never end |
| `core_update` | `target`, `from_version`, `to_version`, `pr` | `files_changed`, `files_removed`, `hooks_added`, `hooks_removed`, `settings_removed` | does an update remove what the core removed |
| `install` | `target`, `version`, `steps` | `files_copied`, `stubs_written`, `labels_created` | an install into any repo, step by step |
| `research_run` | `brief`, `model_alias`, `no_reddit` | `tokens_in`, `tokens_out`, `helper_tokens_in`, `helper_tokens_out`, `reddit_urls`, `search_429`, `compactions`, `report_path`, `report_chars`, `reason` (`research_run_reason`), `report_from` (`report_from`), `links`, `last_chars`, `tokens_reasoning`, `tokens_cache_read`, `tokens_cache_write`, `helper_tokens_reasoning`, `helper_tokens_cache_read`, `helper_tokens_cache_write`, `helper_sessions`, `helper_depth`, `unread_sessions` | bridge cost and why a report was rejected |
| `process` | `program`, `pid`, `argv0`, `hidden`, `launched_by`, `mechanism` | `rc` | every process the layer starts; a console window seen |
| `external_call` | `target` (`call_target`), `command` | `rc`, `transient`, `retries`, `timed_out` | which calls fail or are slow, per machine |
| `check` | `check` (`check`), `target` | `findings`, `facts` | a periodic or start-up check and what it found |
| `sweep` | `tickets_read` | none | the brain's sweep pass and its outcomes |
| `test_map` | `sha`, `place` | `tests` | the test-selection map build |
| `keepalive_install` | `task` | `triggers_kept`, `triggers_left_out` | the boot start of the launcher |
| `acceptance` | `rows_total` | `rows_passed`, `rows_failed`, `rows_na` | was the acceptance run before the PR |
| `ticket_write` | `ticket`, `write_ticket_called` | `shape_check` | the brain's writing of a ticket and its shape check |
| `launch` | `offered`, `capacity`, `capacity_used` | `reason` | the launcher's offer and its outcome |
| `claim` | `ticket`, `lease_minutes` | `holder`, `reason` (`claim_reason`) | one atomic claim on a ticket; the loser writes nothing |
| `plan` | `ticket` | `units`, `self_done`, `reason` (`plan_reason`) | ORCH's split of a ticket, re-run when the ticket changes |
| `close` | `ticket`, `pr` | `reason` (`close_reason`) | the workflow that closes a ticket after its PR merges |
| `reconcile` | `run` | `repairs` (`repair`) | the scheduled repair of every mismatch |

Every `step.ended` also carries `outcome` (from `outcome`) and may carry `reason`, from the step's own list plus
`step_ended_any` (`suspended` only with `no_result`, `lease_lost` only with `lost`). A `reason` is always a code (the
list's rule 5): a `reason` with no list written takes no code yet, and the part that first writes one adds its list to
`closed_sets.json` and here.

## Point kinds

| kind | fields | answers |
|---|---|---|
| `turn.used` | `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `usd`, `price_table`, `tickets_held`, `reason` (`turn_reason`) | every dollar and token; cost per ticket, role, model, step, week |
| `test.result` | `test_id`, `outcome`, `skip_reason`, `ms`, `attempt`, `load_verdict` (`load_verdict`) | per test: flaky retries, slow tests, skips by reason |
| `hook.ran` | `hook`, `action` (`hook_action`), `reason` | what a hook did or why it did nothing |
| `run.checked` | `checked_run`, `result` (`run_check_result`) | a live run checked by another session |
| `ticket.transferred` | `from_repo`, `from_number`, `to_repo`, `to_number` | a ticket moved between repos |
| `machine.booted` | none | the gap a boot leaves in every running step |
| `tool.called` | `tool`, `args_head` (≤120), `outcome` (`tool_outcome`), `refused_by` (`refused_by`), `ms` | what agents actually do; refused calls and retries |
| `skill.called` | `skill`, `called_at_step`, `before_first_edit` | is each skill called at its step |
| `report_line.checked` | `label` (`report_label`), `claimed`, `verified`, `accepted`, `first_edit_path` | false report lines; a stop held for a missing call |
| `rule_file.read` | `path`, `sha`, `lines_read`, `lines_total` | does a role read its rules and the glossary in full |
| `context.crossed` | `threshold` (`context_threshold`), `tokens`, `action_after` (`context_action`) | sessions running past their context line |
| `message.sent` | `to_session`, `to_role` (`role`), `kind` (`message_kind`), `delivered`, `recipient_idle_minutes` | who wakes whom, and what a notice to an idle chat costs |
| `handoff.written` | `context_tokens`, `tickets_closed`, `tickets_parked`, `entry_ref` | hand-offs counted, not hand-typed |
| `handoff.read` | `entry_ref`, `found`, `source` | did the next chat get the hand-off |
| `ticket.opened` | `title`, `labels`, `parent`, `weight`, `priority`, `write_ticket_called`, `has_outside_case`, `acceptance_rows` | tickets written without the skill or the outside case |
| `ticket.edited` | `field` (`ticket_field`), `before`, `after`, `write_ticket_called` | every label flip and rewrite; the ready moment |
| `ticket.claimed` | `weight`, `capacity_used` | who holds what; turns split across held tickets |
| `ticket.released` | `reason` (`release_reason`) | a ticket freed and why |
| `ticket.parked` | `on` (`park_on`), `on_ref`, `until` | a parked ticket and the one event that returns it |
| `ticket.unparked` | `by` (`unpark_by`), `on_ref`, `late_minutes` | how late a return came |
| `park.unreadable` | `reason` | a park nobody can return |
| `comment.written` | `comment_kind` (`comment_kind`) | the state that lives in comment text |
| `attempt.counted` | `reason`, `counted`, `in_a_row` | failed attempts, and which were not the ticket's fault |
| `ticket.closed` | `via` (`close_via`), `state_reason` (`state_reason`), `rows_total`, `rows_passed`, `comments_unanswered` | closes past the acceptance check |
| `ticket.reopened` | `reason` | a closed ticket reopened and why |
| `acceptance_row.checked` | `row`, `result` (`row_result`), `place`, `evidence` | each acceptance row's verdict |
| `review.finding` | `axis` (`review_axis`), `rule_or_row`, `fate` (`finding_fate`), `failing_test` | findings per axis with their fate |
| `owner.asked` | `question_ref`, `options` | open questions to the owner |
| `owner.answered` | `question_ref` | how long the owner's answers held work |
| `launch.decided` | `offered`, `capacity`, `capacity_used`, `result` (`launch_result`), `reason` | why a session started or did not, replayable |
| `guard.fired` | `guard` (`guard`), `verdict` (`guard_verdict`), `segment` (≤120), `rule_id`, `path` | refusals and passes of the kept guards |
| `guard.reversed` | `guard` (`guard`), `fired_event`, `by` | a false fire of a guard |
| `error.raised` | `component`, `error_code`, `failed_open` | a hook, launcher or guard that failed, including failing open |
| `pr.updated` | `pr`, `old_head`, `new_head`, `forced`, `base_moved`, `runs_cancelled` | a push under a running check |
| `pr.conflicted` | `pr`, `files`, `other_pr` | a PR in conflict |
| `pr.conflict_resolved` | `pr`, `files` | a PR conflict resolved |
| `pr.commented` | `pr`, `author_role` (`role`), `answered` | review comments with no reader |
| `main.red` | `sha`, `test_ids`, `introduced_by_pr` | red on main, its own ticket |
| `main.reverted` | `sha`, `reverted_sha`, `reason` | main reverted to a sha and why |
| `doc.edited` | `path`, `doc_kind` (`doc_kind`), `commit`, `required_skill_called`, `term_or_number` | record edits and the skill each needs |
| `term.missing` | `term`, `where` | a report word not in the glossary |
| `drift.found` | `target`, `drift` (`drift`), `path`, `pattern_id` | a target out of step with its core |
| `setting.read` | `key`, `value`, `source` (`setting_source`) | where a setting came from |
| `value.measured` | `name`, `value`, `unit`, `source` | every machine number: latencies, RAM, CPU, rates |
| `view.read` | `view`, `item` | the read marks on a feed |
| `step.progressed` | none | progress of a long step, with its run and counters |
| `machine.resumed` | `gap_minutes`, `cause` (`resume_cause`) | a machine woke after a gap; steps that crossed it read the gap |

## Refusals

`orch_it.events.schema.validate` raises `EventError` with one of these codes (its `field` names the field, dotted for a
detail field): `not_an_object`, `unknown_field` (a top-level field outside the envelope), `missing_field`, `null_field`
(null where the envelope forbids it), `bad_type`, `unknown_kind`, `unknown_step`, `step_missing` (a `step.*` event with
no `step`), `bad_at`, `at_without_zone`, `bad_repo` (not `owner/name`), `bad_code` (a value outside its code list),
`missing_outcome`, `bad_outcome`, `too_long`. Every value is plain JSON (string keys, finite numbers, UTF-8 text),
else `bad_type`; `ticket` is a positive integer; `at` is `YYYY-MM-DDTHH:MM:SS[.ffffff]` with `Z` or `±HH:MM` (a leap second
is refused). A code written `<prefix>:<id>` in a list (`machine:<id>`) takes any value
after its prefix.

## Commands

`python -m orch_it [--store PATH] events <command>`; the store is `--store`, else the `store` setting, else the per-user data folder. An error prints `error: <code>` to stderr and exits 2.

- `query "<sql>" [--param name=value ...]`: read-only SQL over the table `events`, printed as a text table.
- `ticket-cost --repo R --ticket N`: the cost of a ticket from its ready edit to its close per role, direct or shared, with `% week`.
- `session-cost [--session ID] [--repo R] [--since ISO] [--until ISO]`: dollars, `% week`, tokens and minutes per session and model, subagents under their parent, totals.
- `limit-rate`: dollars at API prices per 1 % of the weekly limit between consecutive `weekly_limit_pct` readings.
- `measure-limit <pct> [--repo R] [--role owner] [--note TEXT]`: record a reading of the usage card as `value.measured`.
- `set-limit-rate <usd_per_pct> --plan NAME --measured DATE`: save the setting `weekly_limit_usd_per_pct`.
- `import <transcript folder> --repo R [--role worker] [--subagent-role ROLE] [--ticket N] [--session ID] [--quiet-minutes 30]`: write the events of Claude Code transcripts.
- `import-github [--repo R] [--since ISO]`: write a `test_run` pair, a `github_queue` wait and the `test.result` rows per required job of the repo's `tests` workflow runs (default repo `Rasslabsya4el/orch-it`); a second import writes nothing.
- `test-places [--repo R]`: per place and OS, the runs, their outcomes, the median run minutes and the median GitHub queue minutes.

`python -m orch_it [--store PATH] tests run [--ids-file F] [--place P] [--work-tree PATH] [--repo R] [--ticket N] [--role script]`:
run the repo's test command once at a test place, write its `test_run` pair and one `test.result` per test case, and
print `run=… place=… outcome=… reason=… collected=… failed=…`; exit 0 on passed, 1 on failed, 3 on any other outcome.
`--place machine:<id>` forces a run to a machine of the setting `test_machines` (a list of `id`, `host`, `os`, `checkout`, optional `capacity`, `python`, `origin`, `ssh_options`, `on`): one `ssh` connection, the commit checked out in a free slot of the machine (`checkout`, then `checkout-1` ... one per unit of `capacity`; cloned from `origin`, default the work tree's `origin`), the caller's work tree untouched. A refused run's end carries `refused_step` in its detail (`clone`, `fetch`, `commit`, `checkout`, `clean`, `busy`, `not_ours`, `connect`, `start_timeout`).

`python -m orch_it guards scan (--range A..B | --tree [--rev REV] | --all) [--work-tree PATH] [--machine-independent] [--allow-email ADDR ...] [--allow-email-of LOGIN ...] [--words-file PATH]`:
look for private data (this machine's home folder or host name, any user's home folder, GitHub, Anthropic, OpenAI and AWS keys, private keys, addresses of people, the words of `private_words_file`) in the lines, file paths, messages, authors and committers of the commits of a range (`--all`: everything reachable from HEAD) or in every tracked file of a revision (`--tree`).
It prints one line per hit, `<sha12> <where> <file or ->:<line or -> <kind>` (the value found is never printed; a value in a file path shows as `<kind>`), then `hits=<n> commits=<n>`; exit 0 with no hit, 1 with hits, 2 with `error: <code>`.
`--machine-independent` leaves out what is this machine's own (its home folder, host name and words); `--allow-email-of LOGIN` allows that GitHub account's public email (`gh api users/LOGIN`), else `warning: email_of_unreadable: LOGIN`.

`python -m orch_it labels sync [--repo R]`: make the repo's labels match the layer's set (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`, `parked`, `priority`, each with a colour and description from `labels/labels.json`; default repo the work tree's GitHub origin): a missing label is created, one with another colour, description or letter case is updated, an equal one is kept, every other label is listed `foreign` and never changed or deleted; prints `label`, `action` rows.
