# The merge line merges ready pull requests itself, in batches, with no GitHub auto-merge

Status: accepted

Ready pull requests land on a repo's main line through the merge line, which merges them itself. It takes up to `batch_size` ready members (default 4) onto one branch from `main` and runs the required suite once on that tree; a green batch merges whole, so `main` is the tree the pass tested, and a red batch is split in halves and each half checked again, down to the one red pull request, which goes back to its ticket (Rasslabsya4el/orchestration-layer#414, item 5). The line neither sets nor reads GitHub's auto-merge (`gh pr merge --auto`, `pull.auto_merge`), so the repo setting "Allow auto-merge" stays off. ADR 0008 is superseded by this one: it kept auto-merge on the line's waiting members, which needs that setting on and a line run to give it back, and it made one full pass per member.

## Considered Options

- **GitHub's merge queue**: not offered to a personal repo (ADR 0008, Considered Options), so ordering and batching stay the line's.
- **Parallel speculative passes, one per member**: a batch costs one full pass where a speculative pass costs one per member (Rasslabsya4el/orchestration-layer#414, item 5).

## Consequences

- GitHub merges no pull request of this repo by itself. A green pull request waits until a run of the line takes it, so the launcher's schedule now carries every merge.
- A red batch costs the passes of its halving; the one red member is named on its ticket, and the others land in the batch that goes green.
