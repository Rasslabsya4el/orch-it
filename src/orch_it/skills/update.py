"""The update (#397 decision 4): `skills check` and `skills update`.

`check` fetches his upstream (`main` by default) into a cache folder outside any work tree, reads the
pinned commit's skills and the new ones from their blobs, makes sure the pinned tree is the one the
manifest records file by file, and classifies every skill (`orch_it.skills.classify`), with the
renames git reports between the two commits.

`update` turns a check with a change into one pull request: on a branch `skills-update/<commit>`
of the work tree it writes the new copies and manifest (added skills copied, the names his upstream
dropped recorded as `gone`), commits, pushes, and opens the pull request whose body lists every skill
with its class and the references check of decision 5. A renamed, removed or contract-changed skill,
his copy or one left out, labels it `needs-triage`: it waits for the brain. Only `text_changed` and
`added` set it to merge when green. The work tree goes back to the branch it was on.

The forge (GitHub through `gh`) sits behind `Forge`, so the tests give a fake one.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from orch_it import settings
from orch_it.skills import copies, references
from orch_it.skills.classify import Change, classify
from orch_it.skills.copies import Manifest, SkillsError, Tree

UPSTREAM = "https://github.com/mattpocock/skills"
BRANCH_PREFIX = "skills-update/"
TRIAGE_LABEL = "needs-triage"
TRIAGE_CLASSES = ("renamed", "removed", "contract_changed")
GIT_TIMEOUT_SECONDS = 300
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Check:
    manifest: Manifest
    new_commit: str
    new_tree: Tree
    changes: list[Change]

    @property
    def changed(self) -> list[Change]:
        return [c for c in self.changes if c.cls != "same"]

    def copied(self, change: Change) -> str:
        """`copy`, `not copied (<why>)`, or `new` for an added skill."""

        old = change.old_name or change.name
        if change.cls == "added":
            return "new"
        if old in self.manifest.not_copied:
            return f"not copied ({self.manifest.not_copied[old]['why']})"
        return "copy"

    @property
    def needs_triage(self) -> bool:
        return any(c.cls in TRIAGE_CLASSES for c in self.changes)


@dataclass
class Done:
    status: str               # no_change, already_open, opened
    new_commit: str
    pr: str | None = None
    labels: list[str] = field(default_factory=list)


def _run(cwd: Path, *args: str, check_code: str = "git_failed") -> str:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=GIT_TIMEOUT_SECONDS,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SkillsError(check_code, f"git {args[0]}: {e}") from None
    if done.returncode != 0:
        raise SkillsError(check_code, f"git {' '.join(args[:2])}: "
                                      f"{done.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return done.stdout.decode("utf-8", "replace").strip()


def cache_folder(upstream: str) -> Path:
    return settings.data_folder() / "skills-upstream" / hashlib.sha256(upstream.encode()).hexdigest()[:16]


def fetch(cache: Path, upstream: str, ref: str, pinned: str) -> str:
    """The commit `ref` of `upstream` names now; the pinned commit and it are in the cache after."""

    if not (cache / "HEAD").exists():
        cache.mkdir(parents=True, exist_ok=True)
        _run(cache, "init", "-q", "--bare")
    _run(cache, "fetch", "-q", "--no-tags", upstream, f"+{ref}:refs/upstream/{ref}", check_code="fetch_failed")
    if subprocess.run(["git", "cat-file", "-e", f"{pinned}^{{commit}}"], cwd=cache, capture_output=True,
                      creationflags=NO_WINDOW).returncode != 0:
        _run(cache, "fetch", "-q", "--no-tags", upstream, pinned, check_code="fetch_failed")
    return _run(cache, "rev-parse", f"refs/upstream/{ref}")


def renames(cache: Path, old: str, new: str) -> dict[str, str]:
    """Skill folders git sees renamed between the two commits: old path -> new path."""

    out = _run(cache, "diff", "--name-status", "-M", "--diff-filter=R", old, new, "--", copies.SKILLS_ROOT)
    found = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1].endswith("/" + copies.SKILL_FILE) and parts[2].endswith("/" + copies.SKILL_FILE):
            found[parts[1].rsplit("/", 1)[0]] = parts[2].rsplit("/", 1)[0]
    return found


def check(repo: str | Path, *, upstream: str | None = None, ref: str = "main", cache: Path | None = None) -> Check:
    repo = Path(repo)
    manifest = Manifest.load(repo)
    if manifest is None:
        raise SkillsError("bad_manifest", f"{copies.MANIFEST_NAME}: missing")
    upstream = upstream or manifest.upstream
    cache = cache or cache_folder(upstream)
    new_commit = fetch(cache, upstream, ref, manifest.commit)
    old_tree = copies.read_tree(cache, manifest.commit)
    recorded = {e["path"]: e["files"] for group in (manifest.skills, manifest.not_copied) for e in group.values()}
    actual = {path: {rel: copies.blob_hash(data) for rel, data in files.items()} for path, files in old_tree.items()}
    if recorded != actual:
        raise SkillsError("manifest_mismatch", f"the manifest is not his tree at {manifest.commit[:12]}")
    new_tree = old_tree if new_commit == manifest.commit else copies.read_tree(cache, new_commit)
    changes = classify(old_tree, new_tree, renames=renames(cache, manifest.commit, new_commit))
    return Check(manifest, new_commit, new_tree, changes)


def render(found: Check) -> str:
    lines = [f"upstream {found.manifest.upstream}: pinned {found.manifest.commit[:12]}, now {found.new_commit[:12]}"]
    for change in found.changed:
        paths = f"{change.old_path or '-'} -> {change.new_path or '-'}"
        why = f"  {', '.join(change.why)}" if change.why else ""
        lines.append(f"{change.cls:17} {change.name:30} {found.copied(change)}  {paths}{why}")
    counts = {}
    for change in found.changes:
        counts[change.cls] = counts.get(change.cls, 0) + 1
    lines.append(" ".join(f"{cls}={n}" for cls, n in sorted(counts.items())))
    lines.append("no change" if not found.changed else
                 ("needs-triage: waits for the brain" if found.needs_triage else "merges when green"))
    return "\n".join(lines) + "\n"


def new_manifest(found: Check) -> Manifest:
    old = found.manifest
    not_copied = {name: e["why"] for name, e in old.not_copied.items()}
    gone = dict(old.gone)
    at = found.new_commit[:12]
    for change in found.changes:
        if change.cls == "renamed" and change.old_name != change.name:
            if change.old_name in not_copied:
                not_copied[change.name] = f"{not_copied.pop(change.old_name)} (renamed from {change.old_name})"
            gone[change.old_name] = f"renamed to {change.name} upstream at {at}"
        elif change.cls == "removed":
            gone[change.name] = f"removed upstream at {at}"
    built = Manifest.build(found.new_tree, upstream=old.upstream, commit=found.new_commit, not_copied=not_copied)
    built.gone = {name: what for name, what in sorted(gone.items())
                  if name not in built.skills and name not in built.not_copied}
    return built


def pr_body(found: Check, problems: list[str]) -> str:
    rows = ["| skill | class | in the repo | upstream path | why |", "|---|---|---|---|---|"]
    for change in found.changes:
        held = found.copied(change)
        if change.cls == "added":
            held = "copied, called by no role"
        path = change.new_path or change.old_path
        if change.cls == "renamed":
            path = f"{change.old_path} -> {change.new_path}"
        rows.append(f"| `{change.name}` | {change.cls} | {held} | `{path}` | {', '.join(change.why) or '-'} |")
    if found.needs_triage:
        rule = (f"Label `{TRIAGE_LABEL}`: a skill is renamed, removed or changed in what it takes or returns."
                " It waits for the brain; it is not merged by itself.")
    else:
        rule = "Only `text_changed` and `added`: it merges when `linux` and `windows` are green."
    refs = ("references: none outside the repo's skills" if not problems else
            "references (decision 5; the tests are red until these are fixed in this pull request):\n"
            + "\n".join(f"- {p}" for p in problems))
    return "\n\n".join([
        f"His upstream `{found.manifest.upstream}` moved from `{found.manifest.commit}` to `{found.new_commit}`"
        " (#397 decision 4). The copies and `.claude/skills/pocock.json` are rewritten to the new commit.",
        rule, "\n".join(rows), refs,
        "Written by `python -m orch_it skills update`."])


def _clean(repo: Path) -> None:
    if _run(repo, "status", "--porcelain", "--untracked-files=all"):
        raise SkillsError("work_tree_dirty", str(repo))


def update(repo: str | Path, *, forge, upstream: str | None = None, ref: str = "main",
           cache: Path | None = None, dispatch_tests: bool = False) -> Done:
    repo = Path(repo)
    problems = copies.verify(repo)
    if problems:
        raise SkillsError("copies_edited", "; ".join(problems[:5]))
    found = check(repo, upstream=upstream, ref=ref, cache=cache)
    if not found.changed:
        return Done("no_change", found.new_commit)
    branch = BRANCH_PREFIX + found.new_commit[:12]
    already = forge.open_pr(branch)
    if already:
        return Done("already_open", found.new_commit, already)
    _clean(repo)
    back = _run(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _run(repo, "switch", "-q", "-c", branch)
    try:
        manifest = new_manifest(found)
        copies.write(repo, found.new_tree, manifest)
        refs = references.check(repo)
        _run(repo, "add", "-A", str(copies.SKILLS_FOLDER))
        _run(repo, "commit", "-q", "-m",
             f"His skills at {found.new_commit[:12]}: {len(found.changed)} changed (#397 skills update)")
        _run(repo, "push", "-q", "-u", "origin", branch)
    finally:
        _run(repo, "switch", "-q", "--discard-changes", back)
    title = f"His skills move to {found.new_commit[:12]}: " + ", ".join(
        f"{c.cls} {c.name}" for c in found.changed[:4]) + (" ..." if len(found.changed) > 4 else "")
    pr = forge.create_pr(branch, title[:200], pr_body(found, refs))
    done = Done("opened", found.new_commit, pr)
    if found.needs_triage:
        forge.add_label(pr, TRIAGE_LABEL)
        done.labels.append(TRIAGE_LABEL)
    else:
        forge.merge_when_green(pr)
    if dispatch_tests:
        forge.dispatch_tests(branch)
    return done
