# The merge line merges ready pull requests itself, in batches, with no GitHub auto-merge

Status: accepted

Ready pull requests land on a repo's main line through the merge line, which merges them itself. It takes up to `batch_size` ready members (default 4) onto one branch from `main` and runs the required suite once on that tree; a green batch merges whole, so `main` is the tree the pass tested, and a red batch is split in halves and each half checked again, down to the one red pull request, which goes back to its ticket (Rasslabsya4el/orchestration-layer#414, item 5). The line neither sets nor reads GitHub's auto-merge (`gh pr merge --auto`, `pull.auto_merge`), so the repo setting "Allow auto-merge" stays off. ADR 0008 is superseded by this one: it kept auto-merge on the line's waiting members, which needs that setting on and a line run to give it back, and it made one full pass per member.

## Considered Options

- **Keep ADR 0008**: auto-merge on the line's members, turned off while they wait. Rejected because the repo setting is off by the owner's decision (#397 C4), so the line's own `--auto` fails, and a member's auto-merge state is one more thing a killed line can leave behind.
- **Parallel speculative passes, one per member**: rejected because the account's runner concurrency is shared by every repo of the account; a batch costs one pass, a speculative pass per member costs one each.
- **GitHub's merge queue**: not offered to a personal repo, so ordering, batching and re-running would be the line's anyway.

## Consequences

- GitHub merges no pull request of this repo by itself. A green pull request waits until a run of the line takes it, so the launcher's schedule now carries every merge.
- A red batch costs the passes of its halving; the one red member is named on its ticket, and the others land in the batch that goes green.
