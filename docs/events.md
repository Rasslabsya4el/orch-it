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
| `role` | `owner`, `brain`, `orch`, `worker`, `worker-strong`, `reviewer-standards`, `reviewer-spec`, `reviewer-edge`, `researcher-find`, `researcher-how`, `researcher-survey`, `external-researcher`, `launcher`, `github`, `hook`, `script` |
| `outcome` | `passed`, `failed`, `no_result`, `refused`, `lost`, `stopped` |
| `step_ended_any` | `suspended`, `lease_lost` |
| `launched_by` | `launcher`, `owner`, `session`, `boot` |
| `spawn_refusal` | `role_unknown`, `role_not_spawned`, `input_missing`, `issue_unreadable`, `unit_not_found`, `unit_ambiguous`, `diff_unreadable`, `diff_empty`, `worktree_not_git`, `scratchpad_in_git_tree`, `scratchpad_unusable`, `repo_unknown` |
| `spawn_input` | `issue`, `unit`, `diff`, `worktree`, `scratchpad` |
| `session_end_reason` | `turn_ended`, `idle`, `context_ceiling`, `killed`, `outage`, `stopped_by_owner`, `crashed` |
| `delivered` | `commit`, `comment`, `file`, `none` |
| `wait_reason` | `test_place`, `rate_limit`, `owner_answer`, `brain_answer`, `parked`, `blocker`, `review`, `github_queue`, `capacity` |
| `place` | `own`, `machine:<id>`, `github` |
| `test_os` | `linux`, `windows`, `macos` |
| `place_reason` | `earliest_finish`, `nothing_measured`, `forced` |
| `test_run_reason` | `red`, `cut`, `none_collected`, `ids_not_collected`, `summary_missing`, `job_missing`, `busy`, `os_missing`, `place_off`, `out_of_minutes`, `place_vanished`, `superseded` |
| `research_run_reason` | `too_short`, `few_links`, `no_reddit`, `model_error`, `no_reply`, `empty`, `time_bound`, `stalled`, `server_failed`, `bridge_error` |
| `report_from` | `last`, `earlier`, `joined` |
| `review_axis` | `standards`, `spec`, `edge` |
| `unit_reason` | `format`, `result`, `merge` |
| `pr_reason` | `merged`, `red`, `closed_unmerged`, `timeout`, `withdrawn`, `conflict` |
| `call_target` | `gh`, `git`, `github_api`, `bridge_http`, `claude_cli` |
| `github_budget` | `account`, `actions` |
| `check` | `pocock_contract`, `leak`, `guard_install`, `branch_protection`, `hooks_path`, `environment` |
| `claim_reason` | `held_by_other`, `not_ready` |
| `plan_reason` | `ticket_changed`, `not_doable` |
| `close_reason` | `acceptance_missing`, `row_failed`, `unanswered`, `already_closed`, `ticket_changed` |
| `repair` | `lease_released`, `closed`, `unparked`, `rerun`, `red_ticket_opened` |
| `turn_reason` | `price_missing`, `usage_unreadable` |
| `load_verdict` | `code`, `load` |
| `hook_action` | `acted`, `skipped` |
| `run_check_result` | `alive`, `ended`, `failed` |
| `tool_outcome` | `passed`, `failed`, `refused` |
| `report_label` | `tdd`, `diagnosis`, `code-review`, `research`, `writing-for-agents`, `domain-modeling`, `codebase-design`, `report_cap`, `table` |
| `context_threshold` | `context_line`, `ceiling` |
| `context_action` | `handed_off`, `new_work_taken`, `told` |
| `message_kind` | `notice`, `question`, `answer`, `handoff`, `report` |
| `ticket_field` | `title`, `body`, `labels`, `weight`, `priority`, `assignee`, `parent`, `blocked_by` |
| `release_reason` | `closed`, `parked`, `handed_off`, `session_ended` |
| `park_on` | `pr_ended`, `ticket_closed`, `date`, `run_ended`, `owner_answer`, `layer_version` |
| `unpark_by` | `event`, `hand` |
| `wait_end_reason` | `kind_unknown`, `ref_bad`, `until_bad`, `date_after_until`, `until_past`, `ticket_missing`, `ticket_not_open`, `already_parked`, `ref_missing`, `ref_not_pr`, `ref_not_ticket`, `ref_self`, `ref_parent`, `event_passed`, `park_mismatch`, `ticket_changed`, `body_too_long`, `other_way`, `late`, `comment`, `unreadable`, `hand` |
| `park_return` | `due`, `other_way`, `late`, `comment`, `unreadable`, `hand` |
| `hook_reason` | `write_ticket_missing`, `park_by_hand`, `body_unreadable`, `command_unreadable`, `issue_unreadable`, `literal_value`, `literal_pattern`, `bad_setting`, `shape`, `passed` |
| `ticket_rule` | `title_too_long`, `title_parked`, `title_issue_number`, `human_block_missing`, `human_block_not_first`, `human_field_missing`, `human_field_order`, `human_field_wrong_language`, `human_field_path`, `agent_block_missing`, `acceptance_missing`, `outside_case_missing`, `outside_case_no_row`, `same_object_missing`, `events_row_missing`, `weight_missing`, `weight_bad`, `write_ticket_missing`, `park_by_hand`, `body_unreadable`, `command_unreadable`, `issue_unreadable`, `literal_value`, `literal_pattern`, `bad_setting` |
| `park_unreadable` | `line_missing`, `line_bad`, `lines_many`, `label_missing`, `ref_missing` |
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
| `attempt_reason` | `red`, `red_on_main`, `no_result`, `conflict` |
| `line_reason` | `empty`, `next_run`, `bound`, `error`, `lock_busy`, `lock_stale` |
| `line_result` | `merged`, `left`, `starved`, `bound`, `refused` |

## Step ids

Each step id is written as `step.started` and `step.ended` with the envelope `step` set to the id.

| step id | started fields | ended fields | answers |
|---|---|---|---|
| `session` | `model`, `effort`, `agent_file`, `title`, `launched_by` (`launched_by`) | `reason` (`session_end_reason`), `held_tickets`, `checkpoint_written` | cost and time per session; why it ended; tickets left held by a dead session |
| `subagent` | `model`, `effort`, `agent_file`, `title`, `launched_by` (`launched_by`), `unit`, `worktree`, `prompt_has`, `prompt_chars` | `reason` (`session_end_reason`), `held_tickets`, `checkpoint_written`, `delivered` (`delivered`), `report_lines_owed`, `report_lines_present`, `refused` (`spawn_refusal`), `refused_input` (`spawn_input`) | does a delegation give the subagent what it needs; subagents with no report; spawns refused for a missing input |
| `wait` | `reason` (`wait_reason`), `on` | `reason` (`wait_end_reason`) | where the hours go between steps |
| `test_run` | `place` (`place`), `selection`, `commit`, `branch`, `pr`, `workers`, `settings_hash`, `part`, `run_os` (`test_os`), `places_offered`, `chosen` (`place`), `reason` (`place_reason`) | `reason` (`test_run_reason`), `collected`, `passed`, `failed`, `skipped`, `summary_seen`, `billed_minutes`, `start_minutes`, `run_minutes`, `placeholder_tests`, `failed_tests` | did the tests run where and on what; a cut run is no_result, never red |
| `review` | `axis` (`review_axis`), `unit`, `diff_ref` | `findings`, `fixed` | review cost and yield per axis |
| `unit` | `unit`, `assigned_role` (`role`), `model` | `commit`, `note_present`, `reason` (`unit_reason`) | a unit's life from assignment to hand-back or send-back |
| `pr` | `pr`, `branch`, `base`, `head` | `merge_sha`, `reason` (`pr_reason`) | time from PR to merge; PRs that never end |
| `core_update` | `target`, `from_version`, `to_version`, `pr` | `files_changed`, `files_removed`, `hooks_added`, `hooks_removed`, `settings_removed` | does an update remove what the core removed |
| `install` | `target`, `version`, `steps` | `files_copied`, `stubs_written`, `labels_created` | an install into any repo, step by step |
| `research_run` | `brief`, `model_alias`, `no_reddit`, `brief_chars`, `brief_sha`, `model`, `helpers`, `job` | `tokens_in`, `tokens_out`, `helper_tokens_in`, `helper_tokens_out`, `reddit_urls`, `search_429`, `compactions`, `report_path`, `report_chars`, `reason` (`research_run_reason`), `report_from` (`report_from`), `links`, `last_chars`, `tokens_reasoning`, `tokens_cache_read`, `tokens_cache_write`, `helper_tokens_reasoning`, `helper_tokens_cache_read`, `helper_tokens_cache_write`, `helper_sessions`, `helper_depth`, `unread_sessions`, `partial_path`, `minutes`, `opencode_session`, `server_error`, `abort_failed`, `named_path_unwritable`, `error`, `model_error` (≤200) | bridge cost and why a report was rejected |
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
| `line` | `base`, `prs` | `merged`, `left`, `reason` (`line_reason`) | one run of a repo's merge line |

Every `step.ended` also carries `outcome` (from `outcome`) and may carry `reason`, from the step's own list plus
`step_ended_any` (`suspended` only with `no_result`, `lease_lost` only with `lost`). A `reason` is always a code (the
list's rule 5): a `reason` with no list written takes no code yet, and the part that first writes one adds its list to
`closed_sets.json` and here.

## Point kinds

| kind | fields | answers |
|---|---|---|
| `turn.used` | `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `usd`, `price_table`, `tickets_held`, `reason` (`turn_reason`) | every dollar and token; cost per ticket, role, model, step, week |
| `test.result` | `test_id`, `outcome`, `skip_reason`, `ms`, `attempt`, `load_verdict` (`load_verdict`) | per test: flaky retries, slow tests, skips by reason |
| `hook.ran` | `hook`, `action` (`hook_action`), `reason` (`hook_reason`), `rules` (`ticket_rule`) | what a hook did or why it did nothing |
| `run.checked` | `checked_run`, `result` (`run_check_result`) | a live run checked by another session |
| `ticket.transferred` | `from_repo`, `from_number`, `to_repo`, `to_number` | a ticket moved between repos |
| `machine.booted` | none | the gap a boot leaves in every running step |
| `tool.called` | `tool`, `args_head` (≤120), `outcome` (`tool_outcome`), `refused_by` (`refused_by`), `ms` | what agents actually do; refused calls and retries |
| `skill.called` | `skill`, `called_at_step`, `before_first_edit` | is each skill called at its step |
| `skill.shadowed` | `skill`, `copy`, `same_text` | a personal skill of a repo skill's name that replaces it in every session |
| `lock.removed` | `holder`, `since`, `held_minutes` | a stale merge-line lock removed by hand, and whose it was |
| `line.served` | `pr`, `result` (`line_result`), `followed`, `waited_minutes`, `updates` | each head of the merge line: how long it waited and how often it was brought up to date |
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
| `ticket.unparked` | `by` (`unpark_by`), `on_ref`, `late_minutes`, `returned_as` (`park_return`) | how late a return came |
| `park.unreadable` | `reason` (`park_unreadable`) | a park nobody can return |
| `comment.written` | `comment_kind` (`comment_kind`) | the state that lives in comment text |
| `attempt.counted` | `reason` (`attempt_reason`), `counted`, `in_a_row` | failed attempts, and which were not the ticket's fault |
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
| `github.called` | `budget` (`github_budget`), `account`, `budget_remaining`, `budget_limit`, `budget_reset` | one GitHub call of the layer, counted per account per hour (`account`), or a budget reading (`actions`: the Actions token's remaining per repo); a reading carries the `budget_reset` of its window and is no counted call; a call naming no repo is not stored |
| `step.progressed` | none | progress of a long step, with its run and counters |
| `machine.resumed` | `gap_minutes`, `cause` (`resume_cause`) | a machine woke after a gap; steps that crossed it read the gap |

The setting `request_budget_threshold` is the share of GitHub's limit under which a poller waits longer; its default is in `src/orch_it/events/github_budget_defaults.json`.

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
- `import <transcript folder> --repo R [--role worker] [--subagent-role ROLE] [--ticket N] [--session ID] [--quiet-minutes 30]`: write the events of Claude Code transcripts. A top-level session whose last title is the repo's brain title (the setting `repos.<owner/name>.brain_chat_title`, default `Brain: <repo name>`), or that was started with `--agent brain`, is role `brain` whatever `--role` says (orchestration-layer#403 decision 2).
- `import-github [--repo R] [--since ISO]`: write a `test_run` pair, a `github_queue` wait and the `test.result` rows per required job of the repo's `tests` workflow runs (the repo: `--repo`, else the setting `repo`, else the work tree's GitHub origin); a second import writes nothing.
- `test-places [--repo R]`: per place and OS, the runs, their outcomes, the median run minutes and the median GitHub queue minutes.
- `guard-fires`: per guard and week, the denies, passes, reversals and failed-open errors the guards wrote.

`python -m orch_it [--store PATH] tests run [--ids-file F] [--place P] [--work-tree PATH] [--repo R] [--ticket N] [--role script]`:
run the repo's test command once at a test place, write its `test_run` pair and one `test.result` per test case, and
print `run=… place=… outcome=… reason=… collected=… failed=…`; exit 0 on passed, 1 on failed, 3 on any other outcome.
`--place machine:<id>` forces a run to a machine of the setting `test_machines` (a list of `id`, `host`, `os`, `checkout`, optional `capacity`, `python`, `origin`, `ssh_options`, `on`): one `ssh` connection, the commit checked out in a free slot of the machine (`checkout`, then `checkout-1` ... one per unit of `capacity`; cloned from `origin`, default the work tree's `origin`), the caller's work tree untouched. A refused run's end carries `refused_step` in its detail (`clone`, `fetch`, `commit`, `checkout`, `clean`, `busy`, `not_ours`, `connect`, `start_timeout`).

A run that passed at the `github` place while a test of it read a placeholder file (a stub for private data GitHub does not have) does not settle: those tests rerun at another place as a run of their own, whose end carries `placeholder_tests` (the test ids it reran) and, when red, `failed_tests`. A repo's `tests` workflow keeps this contract: every pytest call loads the plugin (`-p orch_it_ids`, the folder `src/orch_it/testruns/pytest_plugin` on `PYTHONPATH`), the job sets `ORCH_IT_PLACEHOLDERS` to the stubs' paths joined by `os.pathsep` (orch-it's workflow reads it from the repository variable of that name; empty: none), and a step before pytest writes the stubs; the plugin marks each test that opens a listed file with a `reads_placeholder` property in the JUnit report.

`python -m orch_it guards scan (--range A..B | --tree [--rev REV] | --all) [--work-tree PATH] [--machine-independent] [--require-noreply-email | --no-require-noreply-email] [--allow-email ADDR ...] [--allow-email-of LOGIN ...] [--words-file PATH]`:
look for private data (this machine's home folder or host name, the default host names an OS gives a machine (upper-case `WIN-` + 8 to 15, `DESKTOP-` + 7 or `LAPTOP-` + 8 letters or digits, on any machine), any user's home folder, GitHub, Anthropic and AWS keys, the keys of known model providers recognised by their shape, private keys, addresses of people, the words of `private_words_file`) in the lines, file paths, messages, authors and committers of the commits of a range (`--all`: everything reachable from HEAD) or in every tracked file of a revision (`--tree`).
It prints one line per hit, `<sha12> <where> <file or ->:<line or -> <kind>` (the value found is never printed; a value in a file path shows as `<kind>`), then `hits=<n> commits=<n>`; exit 0 with no hit, 1 with hits, 2 with `error: <code>`.
`--machine-independent` leaves out what is this machine's own (its home folder, host name and words); `--allow-email-of LOGIN` allows that GitHub account's public email (`gh api users/LOGIN`), else `warning: email_of_unreadable: LOGIN`.
`--require-noreply-email` (`--no-require-noreply-email` turns it off) checks, with `--range` only, that every commit's author and committer e-mail is a GitHub noreply address (`<id>+<login>@users.noreply.github.com`, `<login>@users.noreply.github.com` or `noreply@github.com`); each commit that is not is one hit, printed as `<sha12> author -:- not_noreply` or `<sha12> committer -:- not_noreply`. Without the option the check is on when the repo's setting `repos.<owner/name>.commit_identity` is `noreply` (the default: also with no settings file, or a work tree with no GitHub origin) and off when it is `any`. `--all` is exempt: the whole history is never checked for it, and the option has no effect there.

`python -m orch_it guards reverse <event id> [--by WHO]`: mark a `guard.fired` deny a false fire, writing a `guard.reversed` that names the fired event and who reversed it (so false fires are counted from the store, never from memory); `error: no_such_fire` or `not_a_deny` when the id names no reversible deny.

`python -m orch_it guards install [--work-tree PATH] [--repo R]`: set git's `core.hooksPath` to the repo's tracked `hooks` folder, so `hooks/pre-push` runs on every push; prints the state of the pre-push hook and whether `.claude/settings.json` holds the PreToolUse entry; one `check` step `guard_install` with the facts (`hooks_path`, `changed`, `pre_push_hook`, `claude_hook`). Another `core.hooksPath`, at any level of git's configuration, is never overwritten: `error: hooks_path_taken` names it (exit 2, S09-041); `hook_file_missing` when the repo tracks no `hooks/pre-push`, `not_a_repo`, `git_failed`. Run again it changes nothing and says `already installed`.

`python -m orch_it guards status [--work-tree PATH] [--repo R]`: read `main`'s branch protection (`gh api repos/R/branches/main/protection`, never changed) and the two hooks, print the six facts of decision 5 as a table (`fact`, `ok`, `detail`: `required_checks` exactly `linux` and `windows`, `strict`, `enforce_admins`, `force_push_and_deletion_off`, `pre_push_hook`, `claude_hook`), and write one `check` step each for `branch_protection`, `hooks_path` and `guard_install` (outcome `passed`/`failed`, `findings` the codes of what is wrong, `facts` what was read). Exit 0 when all six are ok, 1 when any is not; a protection that cannot be read (`protection_unreadable`) or does not exist (`branch_unprotected`) fails its four facts and is reported.

`python -m orch_it hook pre-push <remote-name> <remote-url>`: the git pre-push guard (tracked as `hooks/pre-push`). It reads git's `<local ref> <local sha> <remote ref> <remote sha>` lines on stdin and collects the commits the remote lacks (a new ref: every commit reachable from it that no remote-tracking ref of that remote holds). The remote's visibility is read from the GitHub API at push time (`gh api repos/O/N --jq .visibility`); `private` and `internal` pass unscanned, anything else, an unreadable answer and a remote that is no GitHub repo included, counts as public. The commits of a public push are scanned with this machine's patterns (as `guards scan`); a hit refuses the push (exit 1), names `<sha12> <where> <file or ->:<line or -> <kind>` on stderr (never the value) and writes a `guard.fired` deny (`private_data_public`, `rule_id` the kind) per hit; a clean scanned push writes one pass (`rule_id` `clean`). A hook that crashes lets the push through and writes `error.raised` (`component` `pre_push`, `failed_open`); a hit still refuses when the store cannot be written.

`python -m orch_it hook pre-tool-use`: the Claude Code PreToolUse guard. It reads the hook's JSON on stdin (the tool, its command, the session id and the cwd), reads the command into its delete, move and stop actions, judges each against the object (the real path against the repo's worktrees, the real process tree), writes a `guard.fired` per triggered action, and prints the deny decision that refuses the tool call when one is refused; a guard that crashes lets the call through and writes `error.raised` with `failed_open`. It is wired in orch-it's `.claude/settings.json` for the Bash and PowerShell tools.

`python -m orch_it [--store PATH] hook subagent-stop`: the Claude Code SubagentStop hold of report lines (#399 decision 6), wired in orch-it's `.claude/settings.json`. It reads the stopping subagent's own transcript and, for a role with a `report` in the roles settings, holds the stop once (a block decision; a stop already held passes) with every problem in one reason: a skill line (`tdd:`, `diagnosis:`, `code-review:`, `research:`, `writing-for-agents:`, `domain-modeling:`, `codebase-design:`) claimed (plain, in bold or code, or as a table row) with no Skill call that did not fail, a unit that edits a code file of its worktree (`CODE_SUFFIXES` of `orch_it.roles.hold`; the scratchpad is outside it) with no `tdd:` line, a line the role owes missing, a report over the role's `cap_lines`, or a context past its `context_line` with no `table: <path>` naming a file that exists. It writes one `report_line.checked` per line judged, `context.crossed` when the context line was passed, and, when the stop goes through, the end of the `subagent` step its prompt's `Spawn:` line names (outcome `passed`, or `failed` with a problem left). A role with no `report`, and the brain (a role with no inputs, never spawned), is not read; a payload that is no object, a transcript that cannot be read or an error of the hook lets the stop through (`error.raised`, component `subagent_stop`, `payload_unreadable` or `transcript_unreadable`, `failed_open`); a store that cannot be written never changes the verdict.

`python -m orch_it [--store PATH] hook brain`: the brain's session hook (#399 decision 2, orchestration-layer#403 decision 2), wired in orch-it's `.claude/settings.json` at SessionStart and UserPromptSubmit. The brain is the session whose title (the payload's `session_title`, else the last `custom-title` record of its transcript) is exactly the repo's brain title (the setting `repos.<owner/name>.brain_chat_title`, default `Brain: <repo name>`), else one started with `--agent brain` (the payload's `agent_type`, or the `agentSetting` of its transcript's head). For the brain, at SessionStart and at the first prompt of a session that has not had them (a mark per session id in the data folder's `brain-hook`, kept 7 days), it adds the brain's rules file (docs/agents/brain.md) as the default branch of `origin` holds it and the newest comment of the hand-off issue, and writes `handoff.read` (`entry_ref` `<owner/name>#<issue>`, `found`, `source` `hand-off issue`); on each prompt past the brain's `context_line` of the roles settings (its latest turn's prompt) it adds a reminder, and writes `context.crossed` (threshold `context_line`, `action_after` `told`) at the turn that crosses it. For an interactive chat (no `agent_id`, no other `agent_type`, no `sessionKind` `bg` in its transcript's head) whose title starts with `brain` (any case) or is another repo's brain title, but is not exactly this repo's, it adds one line asking the owner to rename the chat to exactly the brain title. It never stops a prompt or a start: a payload that is no object, a transcript that cannot be read (no transcript path included), rules missing on the default branch, a hand-off that cannot be read or any error lets it through with what could be said and writes `error.raised` (component `brain_hook`, `payload_unreadable`, `transcript_unreadable`, `rules_missing`, `handoff_unreadable` or the error's name, `failed_open`).

`python -m orch_it labels sync [--repo R]`: make the repo's labels match the layer's set (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`, `parked`, `priority`, `brain-handoff`, each with a colour and description from `src/orch_it/labels/labels.json`; default repo the work tree's GitHub origin): a missing label is created, one with another colour, description or letter case is updated, an equal one is kept, every other label is listed `foreign` and never changed or deleted; prints `label`, `action` rows.

`python -m orch_it roles render [--settings PATH] [--work-tree PATH]`: write the `model:` and `effort:` frontmatter lines of each agent file (`.claude/agents/<role>.md`) from the roles settings (`src/orch_it/roles/roles.json`, or `--settings`: per role its `model`, optional `effort` and `fallback` model, each a full model id with a full row in the price file), every other byte of the file kept; prints `file`, `action` (`changed`, `same`) rows. A file of no role is left alone; one with no frontmatter (`error: frontmatter_missing`) or not UTF-8 (`agent_unreadable`) stops it before anything is written; a settings file that cannot be read is `settings_unreadable`, a bad value `bad_setting` naming it. `tests/test_roles_settings.py` fails when an agent file's frontmatter differs from the settings, a file names no role, or a model of the settings, primary or fallback, has no full row in the price file.

`python -m orch_it [--store PATH] spawn <role> --issue N [--unit ID] [--diff F] [--worktree PATH] [--scratchpad PATH] [--repo R]`: the one way a role's subagent is started (#399 decision 3). It fills the role's `inputs` of the roles settings: the ticket fetched with `gh issue view` (body, labels, comments) into `issue-<N>.md`, the unit's row of the plan (a data row of a table whose first cell is the id, from the newest comment that has one, else the body; two in one text are `unit_ambiguous`), the diff (a file, or a git range of the worktree written to `diff.patch`), the worktree (default: the work tree around the folder it runs in) and the scratchpad (the spawn's own folder `<scratchpad>/<run>/`, default `spawn/` of orch-it's data folder, never inside a git work tree), every path absolute; an input the role does not take is left out. It prints the prompt for the Agent tool, whose subagent type is the role's name: the role's agent file, `Spawn: <run>`, one line per input, then the role's report rules (`Report cap:`, `Report lines owed:`, `Context line:`) as the stop hold reads them. A missing input is `error: input_missing: <input>` (exit 2); one that cannot be used is `issue_unreadable`, `unit_not_found`, `unit_ambiguous`, `diff_unreadable`, `diff_empty`, `worktree_not_git`, `scratchpad_in_git_tree` or `scratchpad_unusable`, each naming its input; `repo_unknown` (no `--repo`, no GitHub origin, or no `owner/name`), `role_unknown` and `role_not_spawned` name none. Every spawn whose repo is known writes `step.started` of step `subagent` (run `spawn-<id>`, `prompt_has` the inputs filled); a refused one also its `step.ended`, outcome `refused`, `refused` (its code) and `refused_input`; a store that cannot be written never hides the answer. The brain, a role with no inputs, is the owner's chat: the command does not take it.

`python -m orch_it [--store PATH] tickets park <n> <kind> <ref> --until <YYYY-MM-DD> [--repo R] [--role script]`: the one way a ticket is parked: the line `Parked: <kind> <ref> until <date>` (kind one of `park_on`) and the `parked` label in one write, every other label kept; a park that could never return is refused with a `wait_end_reason` code and writes a `wait` pair ended `refused`; a park writes `ticket.parked` and starts its `wait` (run `park:<repo>#<n>@<label time>`). `tickets unpark <n> [--repo R] [--role script]` takes line and label off together (`ticket.unparked` by `hand`).

`python -m orch_it [--store PATH] tickets parks [--repo R] [--role script]`: the one reader (the setting `owner_login` names whose comment answers an `owner_answer` park, default the repo owner): every open ticket with the `parked` label, reported `waiting`, `due` (back to its queue), `other_way`, `late`, `comment`, `unreadable` (each to the brain: `needs-triage` and a comment) or `failed` (read again next run); a `pr_ended` park whose pull request ended is `followed`: the follow moves the ticket (#400); writes the end of each `wait`, `ticket.unparked`, `park.unreadable` or a `step.progressed`.

`python -m orch_it tickets queue <name> [--repo R]`: the tickets of a queue in the order they are taken (orchestration-layer#403 decision 3), `<name>` `ready` or `sweep`: `ready` is what the dispatcher offers (open, `ready-for-agent`, not `parked`), `sweep` what the brain's sweep works (open, `needs-triage`), each `priority` first, then by number; pull requests and the brain's hand-off issue (the `brain-handoff` label, or the number the setting `brain_handoff_issue` names) are never in one; prints `ticket`, `title`, `labels` rows. Writes no event.

`python -m orch_it brain handoff ensure [--repo R]`: the repo's hand-off issue made so (orchestration-layer#403 decision 3): the `brain-handoff` label in the repo, one open issue carrying it (the one the setting `repos.<owner/name>.brain_handoff_issue` names; else the oldest open one with the label; else a new `Brain hand-off`), the label on it, the issue pinned, the setting naming it; a second run changes nothing. Prints `brain hand-off #<n>: <actions>` (`label_created`, `created`, `labelled`, `pinned`, `setting_written`) or `nothing to change`, and names any other open issue with the label. GitHub refusing the pin (at most three pinned issues) is `error: pin_refused`. Writes no event.

`python -m orch_it [--store PATH] brain handoff write --file <path> [--ticket N] [--repo R]`: one hand-off: the file's text posted as a comment on the hand-off issue (with `Ticket in work: #N` when a ticket is named, and a one-line pointer back on that ticket), then `handoff.written` (role `brain`, `entry_ref` `<owner/name>#<issue>`). Refused: `handoff_issue_unset` (run `ensure`), `handoff_issue_missing`, `handoff_empty`, `handoff_too_long` (over 65,000 characters), `handoff_unreadable`. A store that cannot be written is said on stderr; the hand-off stands.

`python -m orch_it [--store PATH] skills shadowed`: the SessionStart hook of `.claude/settings.json`. It reads the hook's JSON from stdin, compares the project's skills (`CLAUDE_PROJECT_DIR`, else the work tree around `cwd`) with the personal ones (`$CLAUDE_CONFIG_DIR/skills`, default `~/.claude/skills`), letter case aside, and writes one `skill.shadowed` per clash: `copy` true for his copy, false for ours, null when the manifest cannot be read; `same_text` when the two `SKILL.md` are byte-equal. A resumed or compacted session writes nothing new. It prints nothing and exits 0 whatever happens, an error on stderr.

`python -m orch_it skills check [--ref main] [--work-tree PATH]`: fetch Pocock's upstream (`upstream` of `.claude/skills/pocock.json`) into a cache in the per-user data folder and give each of his skills its class against the manifest (`same`, `added`, `renamed`, `removed`, `text_changed`, `contract_changed`): one line per changed skill, the skills per class, the counts, then `no change`, `needs-triage: waits for the brain` or `ready-for-agent: no copy waits for the brain`.

`python -m orch_it skills report [--ref main] [--work-tree PATH] [--repo R]`: the daily run of the `skills-update` workflow (a schedule and on demand). On a change it keeps one issue per upstream commit, "His skills moved to <commit>", a ticket in the shape of `orch_it.tickets` (the human block in the language set by `human_language`; the changed skills in a class table and the unchanged ones on one line; acceptance rows, `Outside case:`, `Same object:`, `Not in this ticket:`, `Weight:`; at most 200 changed skills in the table) whose shape rules run on it at write, opened with the label `needs-triage` when a skill is added, a copy is renamed, removed or changed in contract, or the body breaks a shape rule (the rules listed in it), else `ready-for-agent`; a later run updates that issue's body (its labels are the brain's from then on), does not open again one the brain closed, and closes the open issue of an older upstream commit as superseded; with no change, it closes the issue of the commit the copies are now at. It opens no pull request: one opened with a workflow's token starts no workflow, so its required checks never come. Prints `no change`, `issue: <url> <label>`, `issue already open, body updated: <url>` or `issue closed by the brain for this commit, not opened again: <url>`. Where the language is read from: see the skill `write-ticket`.

`python -m orch_it skills update [--ref main] [--work-tree PATH] [--repo R]`: run by ORCH under the owner's `gh` when it works the issue of `skills report`. On a change that does not wait for the brain, it builds the new copies and manifest on `origin/main` (the work tree is not touched), pushes them to `skills-upstream/<upstream commit>-<main commit>` and opens one pull request listing every skill with its class; its own tests run and it merges as any pull request. A change that waits for the brain (a skill added, a copy renamed, removed or changed in contract, or the issue's body breaks a shape rule) opens no pull request and pushes nothing. An open pull request of the same upstream commit is brought up to date instead (labelled `needs-triage` if it now waits), one closed unmerged is not opened again, an open one of an older commit is closed as superseded. Prints `no change`, `pull request: <url> <labels or no label>`, `pull request already open: <url> ...`, `pull request closed unmerged for this commit, not opened again: <url>` or `no pull request: this upstream commit waits for the brain (its issue is labelled needs-triage)`.

`python -m orch_it [--store PATH] research <brief> [--model ALIAS] [--no-reddit] [--report PATH] [--work-tree PATH] [--repo R] [--ticket N]`:
run the brief through the research bridge (#396): a private opencode server for this run, the model an alias of the
setting `research.models`, the report written to the `docs/research/<name>.md` path the brief names (else `--report`,
else the run's folder), a run with no report leaving what the model wrote beside it as `<name>.partial.md`; the
`research_run` pair and its `step.progressed` events land in the store. It prints `run=… outcome=… reason=… report=…
report_from=… chars=… links=… reddit_urls=… compactions=… minutes=… tokens_in=… helper_tokens_in=…` and exits 0 on
passed, 1 on failed, 3 on any other outcome; a run the settings cannot serve is refused before any server starts
(`error: <code>`, exit 2: `research_not_set`, `unknown_model`, `key_missing`, `brief_unreadable`,
`report_path_outside`, `repo_unknown`, `opencode_missing`, `state_in_git_tree`).

`python -m orch_it research-mcp`: the same bridge as an MCP server on stdin and stdout, registered in `.mcp.json` as
`orch-it-research`; its tools `research_start` and `research_job` run a brief as a background job: `research_job` answers `running` (with
the run's latest progress), `ended` (the run's end as the command line's `--done-file` writes it, plus the report text
and its path) or `gone` (`worker_died`, `worker_lost`: the job's process ended with no answer).

`python -m orch_it tickets check-github --event <file>`: the shape check of the workflow `ticket-shape` on an `issues` event: a ticket that got `ready-for-agent`, or a ready ticket that was edited, is read again and judged by the shape rules; with violations the label comes off, then a comment lists them (the body is never edited); a ticket the layer's own code writes (the line `<!-- orch-it written-by-code -->` among the leading comment lines of its body, opened by a bot or the repo's owner login, and for an edit edited by one: the skills issue, the red-on-main, no-result and workflow-watch tickets) is judged in CI by the shape rules including the language rule (the runner reads the same tree `settings.json` as the writer) but not by the literal rules; the writers check with the shape rules only. Known limits: anyone writing under the owner login can paste the mark (bounded by its place and the editor test), and an organisation-owned repo whose layer account is not the owner login loses the exemption; prints `#<n> kept`, `#<n> removed: <codes>` or `skipped`; an event file that is no JSON object is `error: event_unreadable`.

`python -m orch_it [--store PATH] pr open <ticket> --acceptance <file> [--notes <file>] [--branch B] [--base main] [--work-tree PATH] [--repo R] [--role orch] [--part K/N]`: ORCH's acceptance step (#400 decision 1). The file holds the measured rows, a table `row`, `result`, `evidence`: each row of the ticket once, `after merge` for a row marked so, `pass` or `na` with its evidence for every other (a row marked `after merge` may be left out); a `fail`, a missing, unknown or repeated row refuses (`row_failed`, `row_missing`, `row_unknown`, `row_twice`, `row_bad`, `acceptance_unreadable`, `acceptance_missing`); a ticket that is not there or not open refuses (`ticket_missing`, `ticket_not_open`), so does a branch name git could read as an option or another thing, or `HEAD` (`branch_bad`), or the base itself (`branch_is_base`). One OPEN pull request per ticket at a time (#434): another branch's open pull request of the ticket refuses (`ticket_has_pr`; a second `pr open` that loses the race to park the ticket closes its own new pull request), a merged one never refuses the next; the branch's own is reused when it says `Refs #<ticket>` (else `pr_other_ticket`), its body kept as it was, a fork's branch of the same name is never the ticket's, a ticket parked on anything else refuses (`parked_elsewhere`), a closing keyword (`Closes #N`, `fixes owner/repo#N`, `GH-N`, an issue link) in a commit past the base, the ticket's title or a reused pull request's title or body refuses (`closing_keyword`). Then the branch is pushed (never forced; `push_failed`, `git_failed`), the pull request opened with `Refs #<ticket>` (and, with `--part K/N`, a line `Part: K of N` after it: `bad_part` refuses a K/N that is not 1 <= K <= N) or reused, its head checked to be the commit the rows were measured on (`head_moved`), the acceptance comment written on the ticket (the rows, that head, the hash of the body), the ready mark on the pull request (the merge line merges it; nothing in GitHub does), and the ticket parked `pr_ended` on it until the day after `merge.pr_max_hours`; prints `pr #<n> opened|reused at <head>: <park line>`. Events: the `pr` step started (run `pr:<repo>#<n>@<ready mark time>`), `acceptance_row.checked` per row run, `comment.written` (`accepted`), and the park's own.

`python -m orch_it [--store PATH] follow [<pr>] [--event <file>] [--base main] [--repo R] [--role github]`: the ticket follows its pull request (#400 decision 3), run by the workflow `follow` on a pull request closed, a review, a review comment, a comment, and an edit or close of a parked ticket (`--event $GITHUB_EVENT_PATH`: the pull request the event is about, or the one the ticket's `pr_ended` park names), by `reconcile`, by the merge line, and by `tickets parks` on a `pr_ended` park whose pull request ended. It reads the pull request and its ticket (`Refs #N`; of several, the one parked on it; with none, the ticket parked on it) from GitHub and moves the ticket once, its write first and refused when the ticket changed since it was read (`raced`: nothing written, the next follow acts): merged: closed with its acceptance rows (`closed`), untouched when it waits on another pull request (`merged_other`), or back to ready for its `after merge` rows only (`after_merge`), or to the brain (`acceptance_missing`, `row_failed`, `ticket_changed`, `unanswered`), or only recorded when already closed (`already_closed`); with no ticket, a skills update's pull request closes its skills issue with the commit's done text (`closed_skills`), else only leaves the line (`merged_alone`); closed unmerged: to the brain (`closed_unmerged`); open in the merge line: out of the line (a left mark) with the ticket withdrawn (`withdrawn`), edited (`edited`, back to ready), commented on by a person on the pull request (`commented`) or the ticket (`unanswered`, both to the brain), in conflict (`conflict`, back to ready with the files, not counted), red on its head sha (`red`, back to ready with the failing ids, counted; `red_on_main`, parked on the red-on-main ticket listing every one, not counted; `red_in_a_row`, to the brain at `merge.red_in_a_row`), no result (`rerun` once, `no_result` to the brain the second time, never counted), a check pending past `merge.check_stuck_minutes` cancelled (`stuck`), open past `merge.pr_max_hours` (`timeout`, to the brain); else `waiting`, `behind`, `green` or `idle` (not in the line), nothing changed. A second follow of the same state changes nothing. Only the merge line merges in the line's order; the follow merges a pull request that has no line only on the mark-only path (#424: a green, up to date ready-marked pull request on a base that does not require every required job, after the closing-keyword check). Prints `#<pr> <outcome> ticket=#<n> <detail>` or `skipped: <why>`. Events: the `pr` step's end, the `close` pair, the park's `wait` end with `ticket.unparked`, `ticket.closed`, `comment.written`, `attempt.counted`, `pr.commented`, `pr.conflicted`; in the workflow they go to the run's store, uploaded as the artifact `orch-it-events-follow-<run>-<attempt>`.

`python -m orch_it [--store PATH] merge line [--base main] [--work-tree PATH] [--repo R]`: one run of the repo's merge line (#400 decision 2, #414 item 5), on the machine under the owner's `gh`, so every update and merge starts the required checks; the launcher (part 6) runs it on its schedule. It holds the line lock (`merge.lock_wait_minutes`, then `error: lock_busy`; an older lock than `merge.lock_stale_minutes` is `error: lock_stale`, never taken; while it waits it prints `waits: the line of <repo> is held by <holder>`). Its members are the open, non-draft pull requests into the base from this repo with a ready mark no left mark followed, the oldest ready first. The line merges them itself, with no GitHub auto-merge set or read (ADR 0009). Until none is left or `merge.line_minutes` pass: every conflicting member leaves the line at once and its ticket goes back to ready with the files. With `merge.batch_size` over 1 and `--work-tree` a clone of the repo (its origin is the repo), the oldest ready members are batched: they are combined on one branch from the base, each head in line order, and the required suite runs once on that tree through the place rule; the pass replaces no required check: the combined tree is a branch, and the line waits, polling every `merge.poll_seconds` (longer as the request budget falls) and at most `merge.check_minutes` by the clock, until every name of `required_checks` is green on that exact sha, whichever place posted it (the test suite, the PR scan step and the commit e-mail check each under its required check name); a pass that ran only the test suite leaves the other names unposted, so nothing merges; only then is the base moved to the tested commit by one non-forced ref update, which the base's own branch protection (admins included) accepts or refuses and the line never pushes around, and every member of the batch ends merged; a base that protects nothing is held by the line with the same wait and the same refusal; a name still not green when the wait ends lands nothing and stops the run (note `required checks not green on <sha>: <names>`; every member not landed has a turn `bound` with `checks_missing` and stays for the next run, none sent back); a red test or a red required name splits the batch in halves, each half combined again from the base and checked, down to the one red pull request, which goes back to its ticket while the others land; a member that conflicts with the members before it in its batch goes in a batch of its own after them; a base that moves under a green pass is combined again, at most `merge.max_updates` times, else `starved`. Otherwise (no clone at `--work-tree`, or `merge.batch_size` 1) one pull request is served at a time, and with a batch wanted but no clone the run first prints `batch off: <why>`; the head is brought up to date when behind (at most `merge.max_updates` times, else `starved`, left for the next run), its required checks of its head sha are waited for at most `merge.check_minutes` (`bound`: left for the next run) with the follow at each poll (a red, no result, a person's comment or an edit of its ticket move the ticket: `left`), and it is merged at its head sha with `merge.merge_method` (`merged`; the follow then closes its ticket at once), or, when GitHub refuses the merge more than `merge.max_updates` times, left for the next run (`refused`). Prints one line per head, `#<pr> <result> waited <minutes> min updates <n> <why>`, then `line of <repo> into <base>: merged=<n> left=<n> <reason>` (`empty`, `next_run`: members are left for the next run, `bound`). Events: the `line` step (run `line:<repo>@<start>`; ended `failed` with `error` when the run breaks), one `line.served` per head and per member that left on a conflict, under the same run, `pr.updated` per update, and the follow's own.

`python -m orch_it [--store PATH] main check [--branch main] [--repo R] [--role github]`: main is checked after every merge (#400 decision 4), run by the workflow `main-check` when a `tests` run on the default branch ends, and by `reconcile`. It reads the newest ended run of `tests` on the branch: green on every required job closes every open red-on-main ticket (`green`); red reads the failing ids from the run's reports, closes (when every job ended passed or failed) an open red-on-main ticket none of whose tests failed, and opens one red-on-main ticket (ready for an agent, `priority`, in #398's shape, its failing ids in a fenced block, the pull request of the run's commit named) for the ids no open one lists, never a second for ids already listed (`red`); a run with no result, or red with no test named, runs once more (`rerun`), and no result on its second attempt opens one ticket to the brain for that run (`no_result`, `needs-triage`). When the newest run has not ended, an older ended run may open a red ticket but closes none. Test ids are compared one line each, white space folded, at most 300 characters; a ticket of a failure too big for one body lists what fits and covers the whole set. No skip list exists. Prints `main <outcome> at <sha> closed=#<n>,... opened=#<n>,... [brain=#<n>]`. Events: `main.red` and `ticket.opened` per ticket opened, `ticket.closed` and `comment.written` per ticket closed.

`python -m orch_it [--store PATH] reconcile [--base main] [--repo R] [--role script]`: the scheduled sweep of the merge flow (the workflow `reconcile` hourly; the launcher's schedule on the machine, part 6). It follows every pull request a parked ticket waits on and every member of the merge line, then runs the main check; it starts no work. It runs the same when Actions are off (it says `actions=off: the sweep runs here`). Outside GitHub Actions it also brings into the store the events the workflows wrote in their runs' own stores (their `orch-it-events-*` artifacts, the newest pages of them), each day file copied once under its own name. Prints `actions=on|off...`, then one line per repair `<repair> #<n>` (`closed`, `unparked`, `rerun`, `red_ticket_opened`) and per pull request it could not follow. Events: the `reconcile` step (run `reconcile:<repo>@<time>`; ended `repairs`, `actions`, `imported`, with the outcome `failed` when a pull request could not be followed or the main check broke, each an `error.raised`); an artifact already brought in is not downloaded again (its name is kept in the store's `.imported-artifacts`).

`python -m orch_it [--store PATH] merge unlock [--repo R]`: remove the repo lock of the merge line (the folder `merge-lock/<owner>__<name>` in the per-user data folder) only when it is stale, older than the setting `merge.lock_stale_minutes`; prints `removed the stale lock of <repo>: <holder> since <time>` and writes `lock.removed`. A lock that is not stale is never removed: `error: lock_not_stale` names its holder; no lock is `error: lock_missing`; a lock it cannot remove whole is `error: lock_unwritable` and still names its holder; a repo that is no `owner/name` (or ends in a dot) is `error: bad_repo`.
