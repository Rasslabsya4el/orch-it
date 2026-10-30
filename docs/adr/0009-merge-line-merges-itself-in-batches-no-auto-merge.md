# The merge line merges ready pull requests itself, in batches, with no GitHub auto-merge

Status: accepted

Ready pull requests land on a repo's main line through the merge line, which merges them itself. It takes up to `batch_size` ready members (default 4) onto one branch from `main` and runs the required suite once on that tree; a green batch merges whole, so `main` is the tree the pass tested, once every check named by `required_checks` is green on that tree's sha, and a red batch is split in halves and each half checked again, down to the one red pull request, which goes back to its ticket (Rasslabsya4el/orchestration-layer#414, item 5). The line neither sets nor reads GitHub's auto-merge (`gh pr merge --auto`, `pull.auto_merge`), so the repo setting "Allow auto-merge" stays off. ADR 0008 is superseded by this one: it kept auto-merge on the line's waiting members, which needs that setting on and a line run to give it back, and it made one full pass per member.

## Considered Options

- **GitHub's merge queue**: not offered to a personal repo (ADR 0008, Considered Options), so ordering and batching stay the line's.
- **Parallel speculative passes, one per member**: a batch costs one full pass where a speculative pass costs one per member (Rasslabsya4el/orchestration-layer#414, item 5).

## Consequences

- GitHub merges no pull request of this repo by itself. A green pull request waits until a run of the line takes it.
- The pass replaces no required check. The line pushes the combined tree as a branch and waits, every `merge.poll_seconds` (the poll lengthens as the request budget falls, `budget_poll`) and at most `merge.check_minutes`, until every name in `required_checks` is green on that exact sha, whichever place posted it: the test suite, the PR scan step and the commit e-mail check each under its required check name. Only then does it move `main` to that sha by one non-forced ref update. A pass that ran only the test suite leaves the other names unposted, so nothing merges.
- GitHub's branch protection (admins included) accepts or refuses that ref update; the line never pushes around it. A refusal (note `batch landing refused: <why>`) sends the rest of the run one pull request at a time.
- A repo whose base has no protection is held by the line itself: the same wait, and the same refusal when a name is unposted or red.
- A name not green when the wait ends lands nothing and stops the run (note `required checks not green on <sha>: <names>`; each member's turn is `bound`, detail `checks_missing`); the members stay for the next run and none is sent back. A name red on the tree is a red like a failing test: the batch splits in halves, each half is checked again, and the one member found red alone goes back to its ticket naming the check.
- A red batch costs the passes of its halving; the one red member is named on its ticket, and the others land in the batch that goes green.
