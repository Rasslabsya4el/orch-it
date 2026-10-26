---
name: write-ticket
description: Use when opening a GitHub issue or rewriting an issue's title or body, and when parking a ticket.
---

# Write a ticket

A ticket has two readers. The human decides from five plain lines written in Russian; the agent works from the spec below them, written in English. One shape function (`orch_it.tickets.violations`) judges every ticket: the session hook refuses `gh issue create` and `gh issue edit` with any violation (and a create in a session that never called this skill), and the workflow `ticket-shape` takes `ready-for-agent` off a ready ticket that breaks a rule, with a comment listing every one. Each rule below carries the code both print.

## Template

A filled example: copy its form, not its words.

```
Title: Cart shows the shipping cost before payment

## For the human
- **What:** стоимость доставки показывается в корзине, а не только после ввода карты.
- **Goal:** покупатель видит итоговую цену заказа до оплаты, а не в её конце.
- **Plan:** корзина спрашивает тот же расчёт доставки, что и страница оплаты, как только введён почтовый индекс.
- **Result:** цена доставки в корзине равна цене на оплате на трёх индексах: двух названных и одном вне названных (другая зона); показывает тест расчёта доставки.
- **Undo:** two-way door (один откат); touches каждый заказ, сделанный через корзину.

## For the agent
Facts: the payment page prices shipping with `shipping_quote(postcode)` (checkout/payment.py:88); the cart page has no call to it (checkout/cart.py:31).
Rule: the cart calls the same `shipping_quote(postcode)` as the payment page; no second price table.
Weight: 3
Adds: none

### Acceptance

| row | check | pass |
|---|---|---|
| 1 | cart shipping cost for 10115 | equals the payment page cost for 10115 |
| 2 | cart shipping cost for 20095 | equals the payment page cost for 20095 |
| 3 | cart shipping cost for 50667, a zone none of the named ones is in | equals the payment page cost for 50667 |
| 4 after merge | abandoned carts at the payment step | under 30 % in the weekly report |

Outside case: 50667, a zone none of 10115 and 20095 is in (row 3).
Same object: the payment page's `shipping_quote(postcode)` reads the same postcode; with both, cart and payment show one price from one function.
Not in this ticket: the shipping rates themselves; the payment page's layout.
```

The parts, in order:

- `## For the human`: the five fields `What`, `Goal`, `Plan`, `Result`, `Undo`, bold, each a bullet. Goal says what must become true and for whom (for a defect: what it breaks and for whom). Result names the number across the whole class of cases and the check that shows it, with at least one case outside those the ticket names. Undo is `two-way door (...)` or `one-way door: <what cannot be undone>`, then `touches <who or what>`. A duration reads at a glance: seconds under about 90 s, then minutes, then hours and minutes.
- `## For the agent`: the whole spec: facts with file:line, the rule, the acceptance table, and the line fields below. A row checked only after the merge (a number from `main`, a run on the merged code, something the owner sees in use) is marked `<id> after merge` in its first cell: the ticket then goes back to ORCH after the merge with only those rows.
- `Outside case:`: a real member of the class the named cases do not cover, or a planted one (a throwaway case the check must catch), and the acceptance row that checks it as `(row <id>)`. A change with no class says `Class: none (<why>)` instead.
- `Same object:`: your conclusion from a list of every other part (role, skill, hook, script, setting, open ticket) acting on each object the change touches (a ticket, a label, a file, a merge, a test run), with file:line, and what happens when both run; or `none (<why>)`.
- `Not in this ticket:`: what a reader could expect here and must not do.
- `Weight: <1-10>`: how much work it is (1 a line, 10 a part of the rebuild). Priority is the label `priority`, not a line.
- `Adds:`: the roles and steps the ticket adds (`Adds: step claim, role launcher`) or `none`. A ticket that adds one names the events it writes, and one acceptance row checks them: its check starts with `events`.

## Rules

Title:

- `title_too_long`: at most 80 characters, plain product words that name the change as the human sees it.
- `title_parked`: never the word "parked": a park is the label and its line, below.
- `title_issue_number`: no issue number (`#12`, `owner/repo#12`); no file name either.

Human block:

- `human_block_missing`: the body has the heading `## For the human`.
- `human_block_not_first`: nothing stands above it but blank lines, HTML comments and the park line.
- `human_field_missing`: each of the five fields is there and not empty.
- `human_field_order`: they keep the order What, Goal, Plan, Result, Undo, each once.
- `human_field_not_russian`: their text is Russian; a domain term in backticks and the fixed Undo words (`two-way door`, `touches`) stay as written.
- `human_field_path`: no file path and no file:line in them; those belong to the agent block.

Agent block:

- `agent_block_missing`: the heading `## For the agent` follows the human block.
- `acceptance_missing`: one table with a `check` and a `pass` column, every row filled in.
- `outside_case_missing`: an `Outside case:` line, or `Class: none (<why>)`.
- `outside_case_no_row`: the `Outside case:` line names its acceptance row as `row <id>`.
- `same_object_missing`: a `Same object:` line with what else acts on the objects, or `none (<why>)`; a bare `none` is no answer.
- `events_row_missing`: an `Adds:` line that adds a role or a step has an acceptance row whose check names the `events`.
- `weight_missing`: a line `Weight: <n>`.
- `weight_bad`: the weight is one whole number from 1 to 10.

## Labels

The label set is `orch_it/labels/labels.json`; `python -m orch_it labels sync` makes a repo's labels match it.

- `needs-triage`: the brain decides what happens next.
- `needs-info`: waits for an answer before it can be shaped.
- `ready-for-agent`: shape checked; an agent may take it. Setting it runs the shape check on GitHub.
- `ready-for-human`: needs a person's hands; no agent takes it.
- `wontfix`: decided not to be done.
- `parked`: waits on one named event until a date; written only by the park command.
- `priority`: the owner's push; offered first, kept while parked.

## Parking

A ticket that waits on one event is parked, never left waiting by a word in its title or body:

```
python -m orch_it tickets park <n> <kind> <ref> --until <YYYY-MM-DD>
```

`kind` is one of `pr_ended` (a pull request `#N` merging; closed unmerged sends it to the brain), `ticket_closed` (a ticket `#N` or `owner/repo#N` closed completed; not planned sends it to the brain; never its own parent), `date` (a day), `run_ended` (a GitHub Actions run id or a store run id ending green), `owner_answer` (the owner commenting on the ticket named) or `layer_version` (the repo running orch-it `X.Y.Z`). `until` is the last day it may wait. The command writes the line `Parked: <kind> <ref> until <date>` at the top of the body and the label `parked` in one write, and refuses a park that could never return. Never write that line or that label by hand: the hook refuses it. `python -m orch_it tickets unpark <n>` takes both off. The reader `python -m orch_it tickets parks` returns a ticket whose event came, and sends to the brain (`needs-triage`, a comment) one whose event came the other way, whose `until` passed, that a person commented on, or whose park cannot be read.
